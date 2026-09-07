"""Codex JSONL Session Log Scanner with replay gating, delta calculations, and caching."""

from __future__ import annotations
import glob
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openusage_linux.core.base import (
    DailyUsageSeries,
    ModelUsageSummary,
    ProviderUsageHistory,
    model_summaries_from_buckets,
)
from openusage_linux.core.pricing import ModelPricingStore
from openusage_linux.core.scan_cache import ScanCache


@dataclass
class TokenEvent:
    timestamp: str  # ISO-8601
    model: str
    input: int
    cached: int
    output: int
    reasoning: int
    total: int
    is_fast: bool = False

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "input": self.input,
            "cached": self.cached,
            "output": self.output,
            "reasoning": self.reasoning,
            "total": self.total,
            "is_fast": self.is_fast,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TokenEvent:
        return cls(
            timestamp=data["timestamp"],
            model=data["model"],
            input=data["input"],
            cached=data["cached"],
            output=data["output"],
            reasoning=data["reasoning"],
            total=data["total"],
            is_fast=data.get("is_fast", False),
        )


class CodexLogUsageScanner:
    AUTO_REVIEW_MODEL = "codex-auto-review"
    # Newer Codex CLI also emits top-level token_usage_record objects.
    _LINE_MARKERS = (
        "turn_context",
        "session_meta",
        "task_started",
        "thread_settings_applied",
        "token_count",
        "token_usage_record",
    )

    def __init__(
        self,
        codex_home: Optional[str] = None,
        pricing_store: Optional[ModelPricingStore] = None,
        cache: Optional[ScanCache] = None,
    ):
        self._custom_codex_home = codex_home
        self.pricing_store = pricing_store or ModelPricingStore.get_shared()
        self.cache = cache or ScanCache.get_shared()

    def get_codex_homes(self) -> List[Path]:
        raw = self._custom_codex_home or os.environ.get("CODEX_HOME")
        if raw and raw.strip():
            return [Path(os.path.expanduser(p.strip())) for p in raw.split(",") if p.strip()]
        return [Path.home() / ".codex"]

    def discover_session_files(self) -> List[Path]:
        files: List[Path] = []
        homes = self.get_codex_homes()
        seen_paths: Set[str] = set()

        for home in homes:
            if not home.exists():
                continue
            
            source_dirs = []
            for subdir in ["sessions", "archived_sessions"]:
                d = home / subdir
                if d.is_dir():
                    source_dirs.append(d)
            if not source_dirs:
                source_dirs = [home]

            for d in source_dirs:
                resolved_dir = d.resolve()
                pattern = str(resolved_dir / "**" / "*.jsonl")
                for match in glob.glob(pattern, recursive=True):
                    p = Path(match).resolve()
                    path_key = str(p)
                    if path_key in seen_paths:
                        continue
                    seen_paths.add(path_key)
                    files.append(p)
        return files

    def scan(self, days_back: int = 30) -> Optional[ProviderUsageHistory]:
        files = self.discover_session_files()
        if not files:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        all_events: List[TokenEvent] = []

        for f in files:
            try:
                st = f.stat()
                cached = self.cache.get(str(f), st.st_size, st.st_mtime)
                if cached is not None:
                    events = [TokenEvent.from_dict(d) for d in cached]
                else:
                    events = self.parse_file(f)
                    self.cache.set(str(f), st.st_size, st.st_mtime, [e.to_dict() for e in events])

                for ev in events:
                    ev_dt = self._parse_timestamp(ev.timestamp)
                    if ev_dt and ev_dt >= cutoff:
                        all_events.append(ev)
            except Exception:
                continue

        self.cache.prune(keep_paths=[str(path) for path in files])
        self.cache.flush()
        if not all_events:
            return None

        return self.aggregate(all_events)

    def parse_file(self, file_path: Path) -> List[TokenEvent]:
        events: List[TokenEvent] = []
        previous_totals: Optional[Dict[str, int]] = None
        last_record_usage: Optional[Dict[str, int]] = None
        current_model: Optional[str] = None
        current_tier_is_fast = False
        saw_session_meta = False
        replay_gate_cleared = True
        child_created_epoch: Optional[float] = None

        try:
            handle = open(file_path, "r", encoding="utf-8", errors="ignore")
        except Exception:
            return events

        try:
            for line in handle:
                try:
                    line = line.strip()
                    if not line:
                        continue
                    if not any(marker in line for marker in self._LINE_MARKERS):
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue

                    msg_type = obj.get("type")
                    payload = obj.get("payload")
                    if not isinstance(payload, dict):
                        payload = {}
                    ts_str = obj.get("timestamp", "") if isinstance(obj.get("timestamp"), str) else ""

                    if msg_type == "turn_context":
                        model = self._extract_model(payload)
                        if model:
                            current_model = model
                        continue

                    if msg_type == "session_meta" and not saw_session_meta:
                        saw_session_meta = True
                        if self._is_child_session(payload):
                            replay_gate_cleared = False
                            if ts_str:
                                try:
                                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                    child_created_epoch = dt.timestamp()
                                except Exception:
                                    pass
                        continue

                    if msg_type == "token_usage_record":
                        if not replay_gate_cleared:
                            continue
                        usage = self._extract_raw_usage(
                            payload.get("usage") or payload.get("turn_token_usage")
                        )
                        if not usage or self._is_empty_usage(usage):
                            continue
                        last_record_usage = usage
                        events.append(self._token_event(
                            ts_str,
                            self._extract_model(payload) or current_model,
                            usage,
                            current_tier_is_fast,
                        ))
                        continue

                    if msg_type != "event_msg":
                        continue

                    event_payload_type = payload.get("type")
                    if event_payload_type == "thread_settings_applied":
                        tier = self._extract_service_tier(payload)
                        current_tier_is_fast = tier in ("fast", "priority")
                        continue

                    if event_payload_type == "task_started":
                        if not replay_gate_cleared:
                            started_at = payload.get("started_at")
                            if isinstance(started_at, (int, float)) and child_created_epoch is not None:
                                if started_at >= child_created_epoch:
                                    replay_gate_cleared = True
                        continue

                    if event_payload_type != "token_count":
                        continue

                    info = payload.get("info")
                    if not isinstance(info, dict):
                        info = {}
                    totals = self._extract_raw_usage(info.get("total_token_usage"))

                    if not replay_gate_cleared:
                        if totals:
                            previous_totals = totals
                        continue

                    if totals and previous_totals and totals == previous_totals:
                        continue

                    last_usage = self._extract_raw_usage(info.get("last_token_usage"))
                    if last_record_usage and last_usage == last_record_usage:
                        last_record_usage = None
                        if totals:
                            previous_totals = totals
                        continue
                    if last_usage and not self._is_empty_usage(last_usage):
                        turn_usage = last_usage
                    elif totals:
                        turn_usage = self._subtract_usage(totals, previous_totals)
                    else:
                        continue

                    if totals:
                        previous_totals = totals
                    if self._is_empty_usage(turn_usage):
                        continue

                    events.append(self._token_event(
                        ts_str,
                        self._extract_model(payload) or self._extract_model(info) or current_model,
                        turn_usage,
                        current_tier_is_fast,
                    ))
                except Exception:
                    continue
        finally:
            handle.close()

        return events

    def _token_event(
        self,
        timestamp: str,
        model: Optional[str],
        usage: Dict[str, int],
        is_fast: bool,
    ) -> TokenEvent:
        return TokenEvent(
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            model=model or "gpt-5",
            input=usage["input"],
            cached=min(usage["cached"], usage["input"]),
            output=usage["output"],
            reasoning=usage["reasoning"],
            total=usage["total"],
            is_fast=is_fast,
        )

    @staticmethod
    def _is_empty_usage(usage: Dict[str, int]) -> bool:
        return (
            usage["input"] == 0
            and usage["cached"] == 0
            and usage["output"] == 0
            and usage["reasoning"] == 0
        )

    def aggregate(self, events: List[TokenEvent]) -> ProviderUsageHistory:
        daily_buckets: Dict[str, Dict[str, Any]] = {}
        model_buckets: Dict[str, Dict[str, Any]] = {}
        daily_model_buckets: Dict[str, Dict[str, Dict[str, Any]]] = {}

        def _empty_bucket() -> Dict[str, Any]:
            return {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "total": 0, "cost": 0.0}

        for ev in events:
            date_str = self._event_date(ev.timestamp)
            if date_str not in daily_buckets:
                daily_buckets[date_str] = _empty_bucket()
                daily_model_buckets[date_str] = {}

            if ev.model not in model_buckets:
                model_buckets[ev.model] = _empty_bucket()
            if ev.model not in daily_model_buckets[date_str]:
                daily_model_buckets[date_str][ev.model] = _empty_bucket()

            cost = self.pricing_store.cost_for(
                model=ev.model,
                input_tokens=ev.input,
                cached_tokens=ev.cached,
                output_tokens=ev.output,
                reasoning_tokens=ev.reasoning,
                is_fast=ev.is_fast,
            )

            for b in (daily_buckets[date_str], model_buckets[ev.model], daily_model_buckets[date_str][ev.model]):
                b["input"] += ev.input
                b["cached"] += ev.cached
                b["output"] += ev.output
                b["reasoning"] += ev.reasoning
                b["total"] += ev.total
                b["cost"] += cost

        series = [
            DailyUsageSeries(
                date=k,
                input_tokens=v["input"],
                cached_tokens=v["cached"],
                output_tokens=v["output"],
                reasoning_tokens=v["reasoning"],
                total_tokens=v["total"],
                estimated_cost=v["cost"],
                models=model_summaries_from_buckets(daily_model_buckets.get(k, {})),
            )
            for k, v in sorted(daily_buckets.items())
        ]

        model_usage = model_summaries_from_buckets(model_buckets)

        return ProviderUsageHistory(series=series, model_usage=model_usage)

    @staticmethod
    def _parse_timestamp(timestamp: str) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            # Codex writes UTC timestamps. Treat a legacy naive value as UTC so
            # it can still be compared to the UTC scan cutoff safely.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    @classmethod
    def _event_date(cls, timestamp: str) -> str:
        """Group events by the user's local calendar date."""
        parsed = cls._parse_timestamp(timestamp)
        if parsed:
            return parsed.astimezone().date().isoformat()
        return timestamp[:10]

    @staticmethod
    def _extract_model(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        for key in ["model", "model_name"]:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            val = meta.get("model")
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @staticmethod
    def _is_child_session(payload: Dict[str, Any]) -> bool:
        if payload.get("parent_session_id") or payload.get("forked_from"):
            return True
        agent_role = payload.get("agent_role")
        if agent_role and agent_role != "root":
            return True
        return False

    @staticmethod
    def _extract_service_tier(payload: Dict[str, Any]) -> Optional[str]:
        for container in [payload.get("thread_settings"), payload]:
            if isinstance(container, dict):
                tier = container.get("service_tier")
                if isinstance(tier, str) and tier.strip():
                    return tier.strip().lower()
        return None

    @staticmethod
    def _extract_raw_usage(json_data: Any) -> Optional[Dict[str, int]]:
        if not isinstance(json_data, dict):
            return None
        
        def _get_int(*keys: str) -> int:
            for k in keys:
                v = json_data.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
            return 0

        inp = _get_int("input_tokens", "prompt_tokens", "input")
        cached = _get_int("cached_input_tokens", "cache_read_input_tokens", "cached_tokens")
        out = _get_int("output_tokens", "completion_tokens", "output")
        reasoning = _get_int("reasoning_output_tokens", "reasoning_tokens")
        total = _get_int("total_tokens")
        if total == 0:
            total = inp + out + reasoning

        return {
            "input": inp,
            "cached": cached,
            "output": out,
            "reasoning": reasoning,
            "total": total,
        }

    @staticmethod
    def _subtract_usage(current: Dict[str, int], previous: Optional[Dict[str, int]]) -> Dict[str, int]:
        if not previous:
            return dict(current)
        return {
            "input": max(0, current["input"] - previous.get("input", 0)),
            "cached": max(0, current["cached"] - previous.get("cached", 0)),
            "output": max(0, current["output"] - previous.get("output", 0)),
            "reasoning": max(0, current["reasoning"] - previous.get("reasoning", 0)),
            "total": max(0, current["total"] - previous.get("total", 0)),
        }
