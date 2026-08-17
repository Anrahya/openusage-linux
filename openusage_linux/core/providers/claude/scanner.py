"""Claude Code transcript scanner (port of ClaudeLogUsageScanner).

Scans ~/.claude/projects/**/*.jsonl for assistant token usage. Costs come from
the recorded costUSD when present; otherwise tokens are priced through the
shared model pricing store.
"""

from __future__ import annotations
import glob
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openusage_linux.core.base import (
    DailyUsageSeries,
    ModelUsageSummary,
    ProviderUsageHistory,
)
from openusage_linux.core.pricing import ModelPricingStore

USAGE_MARKER = '"usage"'
_SEMVER_PREFIX_RE = re.compile(r"^\d+\.\d+\.\d")

# Lines carrying an explicit null in any of these fields are unsupported.
_NULL_GUARD_FIELDS = {
    "id", "cwd", "model", "speed", "costUSD", "version", "sessionId",
    "requestId", "isApiErrorMessage", "cache_read_input_tokens",
    "cache_creation_input_tokens",
}


@dataclass
class ClaudeEntry:
    timestamp: str
    input_tokens: int
    cache_write_5m: int
    cache_write_1h: int
    cache_read: int
    output_tokens: int
    is_fast: bool
    message_id: Optional[str]
    request_id: Optional[str]
    is_sidechain: bool
    has_speed: bool
    cost_usd: Optional[float]
    model: Optional[str]

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.cache_write_5m + self.cache_write_1h
                + self.cache_read + self.output_tokens)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "input_tokens": self.input_tokens,
            "cache_write_5m": self.cache_write_5m,
            "cache_write_1h": self.cache_write_1h,
            "cache_read": self.cache_read,
            "output_tokens": self.output_tokens,
            "is_fast": self.is_fast,
            "message_id": self.message_id,
            "request_id": self.request_id,
            "is_sidechain": self.is_sidechain,
            "has_speed": self.has_speed,
            "cost_usd": self.cost_usd,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClaudeEntry":
        return cls(
            timestamp=data["timestamp"],
            input_tokens=data["input_tokens"],
            cache_write_5m=data["cache_write_5m"],
            cache_write_1h=data["cache_write_1h"],
            cache_read=data["cache_read"],
            output_tokens=data["output_tokens"],
            is_fast=data["is_fast"],
            message_id=data.get("message_id"),
            request_id=data.get("request_id"),
            is_sidechain=data.get("is_sidechain", False),
            has_speed=data.get("has_speed", False),
            cost_usd=data.get("cost_usd"),
            model=data.get("model"),
        )


def claude_roots() -> List[Path]:
    roots: List[Path] = []
    seen: Set[str] = set()

    def add(candidate: Path):
        if (candidate / "projects").is_dir() and str(candidate) not in seen:
            seen.add(str(candidate))
            roots.append(candidate)

    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if override:
        for part in override.split(","):
            part = part.strip()
            if not part:
                continue
            path = Path(os.path.expanduser(part))
            if path.name == "projects":
                path = path.parent
            add(path)
        return roots

    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        add(Path(os.path.expanduser(config_home)) / "claude")
    add(Path.home() / ".claude")
    return roots


class ClaudeLogUsageScanner:
    def __init__(self, pricing_store: Optional[ModelPricingStore] = None):
        self.pricing_store = pricing_store or ModelPricingStore.get_shared()

    def discover_session_files(self) -> List[Path]:
        files: List[Path] = []
        seen: Set[str] = set()
        for root in claude_roots():
            pattern = str((root / "projects").resolve() / "**" / "*.jsonl")
            for match in sorted(glob.glob(pattern, recursive=True)):
                path = Path(match).resolve()
                if str(path) not in seen:
                    seen.add(str(path))
                    files.append(path)
        return files

    def scan(self, days_back: int = 30, now: Optional[datetime] = None) -> Optional[ProviderUsageHistory]:
        files = self.discover_session_files()
        if not files:
            return None
        now = now or datetime.now(timezone.utc)
        since = (now.astimezone() - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        entries: List[ClaudeEntry] = []
        for f in files:
            try:
                for entry in self.parse_file(f):
                    parsed = self._parse_timestamp(entry.timestamp)
                    if parsed and parsed >= since:
                        entries.append(entry)
            except Exception:
                continue

        entries = self._dedup(entries)
        return self._aggregate(entries)

    # ── parsing ──────────────────────────────────────────────────────

    def parse_file(self, file_path: Path) -> List[ClaudeEntry]:
        entries: List[ClaudeEntry] = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or USAGE_MARKER not in line:
                        continue
                    if self._has_unsupported_null_field(line):
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    parsed = self._parse_entry(obj)
                    entries.extend(parsed)
        except Exception:
            pass
        return entries

    @staticmethod
    def _has_unsupported_null_field(line: str) -> bool:
        for match in re.finditer(r":\s*null", line):
            prefix = line[:match.start()].rstrip()
            quote_end = prefix.rfind('"')
            if quote_end == -1:
                continue
            quote_start = prefix.rfind('"', 0, quote_end)
            if quote_start == -1:
                continue
            field_name = prefix[quote_start + 1:quote_end]
            if field_name in _NULL_GUARD_FIELDS:
                return True
        return False

    @classmethod
    def _parse_entry(cls, obj: Any) -> List[ClaudeEntry]:
        if not isinstance(obj, dict):
            return []
        timestamp = obj.get("timestamp")
        if not isinstance(timestamp, str) or not cls._parse_timestamp(timestamp):
            return []
        message = obj.get("message")
        if not isinstance(message, dict):
            return []
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return []

        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if not isinstance(input_tokens, (int, float)) or isinstance(input_tokens, bool):
            return []
        if not isinstance(output_tokens, (int, float)) or isinstance(output_tokens, bool):
            return []

        # Version sanity + empty-string identity guard.
        version = obj.get("version")
        if isinstance(version, str) and not _SEMVER_PREFIX_RE.match(version):
            return []
        for value in (obj.get("sessionId"), obj.get("requestId"),
                      message.get("id"), message.get("model")):
            if value == "":
                return []

        speed = usage.get("speed")
        has_speed = "speed" in usage
        if has_speed and speed not in ("fast", "standard"):
            return []
        is_fast = speed == "fast"

        cache_write_5m = 0
        cache_write_1h = 0
        cache_creation = usage.get("cache_creation")
        if isinstance(cache_creation, dict):
            cache_write_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
            cache_write_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
        else:
            legacy = usage.get("cache_creation_input_tokens")
            if isinstance(legacy, (int, float)) and not isinstance(legacy, bool):
                cache_write_5m = int(legacy)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)

        model = message.get("model")
        if not isinstance(model, str) or model == "<synthetic>" or not model.strip():
            model = None
        else:
            model = model.strip()

        cost_usd = obj.get("costUSD")
        if isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)):
            cost_usd = None
        else:
            cost_usd = float(cost_usd)

        message_id = message.get("id") if isinstance(message.get("id"), str) else None
        request_id = obj.get("requestId") if isinstance(obj.get("requestId"), str) else None
        is_sidechain = bool(obj.get("isSidechain"))

        base = ClaudeEntry(
            timestamp=timestamp,
            input_tokens=int(input_tokens),
            cache_write_5m=cache_write_5m,
            cache_write_1h=cache_write_1h,
            cache_read=cache_read,
            output_tokens=int(output_tokens),
            is_fast=is_fast,
            message_id=message_id,
            request_id=request_id,
            is_sidechain=is_sidechain,
            has_speed=has_speed,
            cost_usd=cost_usd,
            model=model,
        )
        entries = [base]

        # Advisor iterations are separate model calls billed inside the line.
        iterations = usage.get("iterations")
        if isinstance(iterations, list):
            advisor_index = 0
            for iteration in iterations:
                if not isinstance(iteration, dict) or iteration.get("type") != "advisor_message":
                    continue
                advisor_model = iteration.get("model")
                if not isinstance(advisor_model, str) or not advisor_model.strip():
                    continue
                breakdown = cls._iteration_breakdown(iteration)
                if breakdown is None:
                    continue
                entries.append(ClaudeEntry(
                    timestamp=timestamp,
                    input_tokens=breakdown["input"],
                    cache_write_5m=breakdown["write5m"],
                    cache_write_1h=breakdown["write1h"],
                    cache_read=breakdown["read"],
                    output_tokens=breakdown["output"],
                    is_fast=is_fast,
                    message_id=f"{message_id}:advisor:{advisor_index}" if message_id else None,
                    request_id=request_id,
                    is_sidechain=is_sidechain,
                    has_speed=has_speed,
                    cost_usd=None,
                    model=advisor_model.strip(),
                ))
                advisor_index += 1
        return entries

    @staticmethod
    def _iteration_breakdown(iteration: Dict[str, Any]) -> Optional[Dict[str, int]]:
        usage = iteration.get("usage")
        if not isinstance(usage, dict):
            return None
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if not isinstance(input_tokens, (int, float)) or isinstance(input_tokens, bool):
            return None
        if not isinstance(output_tokens, (int, float)) or isinstance(output_tokens, bool):
            return None
        return {
            "input": int(input_tokens),
            "write5m": int(usage.get("cache_creation_input_tokens") or 0),
            "write1h": 0,
            "read": int(usage.get("cache_read_input_tokens") or 0),
            "output": int(output_tokens),
        }

    @staticmethod
    def _parse_timestamp(timestamp: str) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    # ── dedup ────────────────────────────────────────────────────────

    @staticmethod
    def _should_replace(candidate: ClaudeEntry, existing: ClaudeEntry) -> bool:
        if candidate.is_sidechain != existing.is_sidechain:
            return not candidate.is_sidechain
        if candidate.total_tokens != existing.total_tokens:
            return candidate.total_tokens > existing.total_tokens
        return candidate.has_speed and not existing.has_speed

    @classmethod
    def _dedup(cls, entries: List[ClaudeEntry]) -> List[ClaudeEntry]:
        primary: Dict[Tuple[Optional[str], Optional[str]], int] = {}
        by_message: Dict[str, List[int]] = {}
        kept: List[ClaudeEntry] = []

        for entry in entries:
            index = len(kept)
            replaced = False

            if entry.message_id is not None:
                key = (entry.message_id, entry.request_id)
                if key in primary:
                    existing_index = primary[key]
                    if cls._should_replace(entry, kept[existing_index]):
                        kept[existing_index] = entry
                    replaced = True
                else:
                    # Secondary collision: same message id replayed under a new
                    # request id (sidechain logs) — only fight when sidechain.
                    for other_index in by_message.get(entry.message_id, []):
                        other = kept[other_index]
                        if entry.is_sidechain or other.is_sidechain:
                            if cls._should_replace(entry, other):
                                kept[other_index] = entry
                            replaced = True
                            break

            if not replaced:
                kept.append(entry)
                if entry.message_id is not None:
                    primary[(entry.message_id, entry.request_id)] = index
                    by_message.setdefault(entry.message_id, []).append(index)
        return kept

    # ── aggregation ──────────────────────────────────────────────────

    def _aggregate(self, entries: List[ClaudeEntry]) -> ProviderUsageHistory:
        daily: Dict[str, Dict[str, Any]] = {}
        models: Dict[str, Dict[str, Any]] = {}
        unknown_models_by_day: Dict[str, Set[str]] = {}

        for entry in entries:
            cost = entry.cost_usd
            if cost is None:
                if not entry.model:
                    continue  # unattributed without recorded cost: excluded
                rates = self.pricing_store.rate_for(entry.model, is_fast=entry.is_fast)
                cost = rates.cost_dollars(
                    input_tokens=entry.input_tokens + entry.cache_write_5m + entry.cache_write_1h,
                    cached_tokens=entry.cache_read,
                    output_tokens=entry.output_tokens,
                    is_fast=entry.is_fast,
                )

            parsed = self._parse_timestamp(entry.timestamp)
            day = parsed.astimezone().date().isoformat() if parsed else entry.timestamp[:10]

            bucket = daily.setdefault(day, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0})
            bucket["input"] += entry.input_tokens + entry.cache_write_5m + entry.cache_write_1h
            bucket["cached"] += entry.cache_read
            bucket["output"] += entry.output_tokens
            bucket["total"] += entry.total_tokens
            bucket["cost"] += cost

            model_key = entry.model or "Unattributed"
            model_bucket = models.setdefault(model_key, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0})
            model_bucket["input"] += entry.input_tokens + entry.cache_write_5m + entry.cache_write_1h
            model_bucket["cached"] += entry.cache_read
            model_bucket["output"] += entry.output_tokens
            model_bucket["total"] += entry.total_tokens
            model_bucket["cost"] += cost

        series = [
            DailyUsageSeries(
                date=day,
                input_tokens=values["input"],
                cached_tokens=values["cached"],
                output_tokens=values["output"],
                total_tokens=values["total"],
                estimated_cost=values["cost"],
            )
            for day, values in sorted(daily.items())
        ]
        model_usage = [
            ModelUsageSummary(
                model=name,
                input_tokens=values["input"],
                cached_tokens=values["cached"],
                output_tokens=values["output"],
                total_tokens=values["total"],
                estimated_cost=values["cost"],
            )
            for name, values in sorted(models.items(), key=lambda item: item[1]["total"], reverse=True)
        ]
        return ProviderUsageHistory(
            series=series,
            model_usage=model_usage,
            unknown_models_by_day={day: sorted(names) for day, names in unknown_models_by_day.items()},
        )
