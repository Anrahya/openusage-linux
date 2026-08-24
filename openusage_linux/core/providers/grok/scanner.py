"""Grok CLI session scanner (port of GrokLogUsageScanner).

Spend tiles come from completed turns in `~/.grok/sessions/**/updates.jsonl`
(or `$GROK_HOME/sessions`). `logs/unified.jsonl` is a capped debug log and is
not a fallback. Coordinator sessions already include subagent usage, so
ledgers whose summary `session_kind` starts with `subagent` are skipped.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from openusage_linux.core.base import (
    DailyUsageSeries,
    ProviderUsageHistory,
    model_summaries_from_buckets,
)
from openusage_linux.core.pricing import ModelPricingStore, ModelRates
from openusage_linux.core.providers.grok.auth import grok_home

COST_TICKS_PER_DOLLAR = 10_000_000_000
COMPLETED_TURN_MARKER = "turn_completed"


@dataclass
class GrokEntry:
    timestamp: datetime
    model: str
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    event_id: Optional[str] = None
    carried_cost: Optional[float] = None

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _object(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


class GrokLogUsageScanner:
    def __init__(self, pricing_store: Optional[ModelPricingStore] = None):
        self.pricing_store = pricing_store or ModelPricingStore.get_shared()

    def sessions_dir(self) -> str:
        return os.path.join(grok_home(), "sessions")

    def scan(self, days_back: int = 30, now: Optional[datetime] = None) -> Optional[ProviderUsageHistory]:
        directory = self.sessions_dir()
        files = [path for path in self._discover_updates(directory) if self._is_coordinator_session(path)]
        if not files:
            return None

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        since = (now.astimezone() - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        entries: List[GrokEntry] = []
        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    entries.extend(self.parse_file(handle.read()))
            except OSError:
                continue
        return self.aggregate(self.dedup(entries), since=since, pricing_store=self.pricing_store)

    @staticmethod
    def _discover_updates(directory: str) -> List[str]:
        if not os.path.isdir(directory):
            return []
        found: List[str] = []
        for root, _dirs, files in os.walk(directory):
            if "updates.jsonl" in files:
                found.append(os.path.join(root, "updates.jsonl"))
        return found

    @staticmethod
    def _is_coordinator_session(path: str) -> bool:
        if os.path.basename(path) != "updates.jsonl":
            return False
        summary_path = os.path.join(os.path.dirname(path), "summary.json")
        if not os.path.exists(summary_path):
            return True
        try:
            with open(summary_path, "r", encoding="utf-8") as handle:
                summary = json.load(handle)
        except Exception:
            return False
        if not isinstance(summary, dict):
            return False
        kind = summary.get("session_kind")
        if not isinstance(kind, str):
            return True
        return not kind.strip().lower().startswith("subagent")

    @classmethod
    def parse_file(cls, text: str) -> List[GrokEntry]:
        entries: List[GrokEntry] = []
        for raw_line in text.splitlines():
            if COMPLETED_TURN_MARKER not in raw_line:
                continue
            try:
                object_ = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(object_, dict):
                continue
            entries.extend(cls._parse_completed_turn(object_))
        return entries

    @classmethod
    def _parse_completed_turn(cls, object_: Dict[str, Any]) -> List[GrokEntry]:
        params = _object(object_.get("params"))
        update = _object((params or {}).get("update")) or _object(object_.get("update"))
        if not update or update.get("sessionUpdate") != "turn_completed":
            return []
        usage = _object(update.get("usage"))
        model_usage = _object((usage or {}).get("modelUsage"))
        timestamp = cls._timestamp(object_, params)
        if usage is None or model_usage is None or timestamp is None:
            return []

        metadata = _object((params or {}).get("_meta")) or _object(object_.get("_meta"))
        event_id = None
        if metadata:
            raw_id = metadata.get("eventId")
            if isinstance(raw_id, str) and raw_id.strip():
                event_id = raw_id.strip()
        top_level_ticks = _number(usage.get("costUsdTicks"))

        entries: List[GrokEntry] = []
        for raw_model, raw_usage in sorted(model_usage.items()):
            model = raw_model.strip() if isinstance(raw_model, str) else ""
            values = _object(raw_usage)
            input_value = _number((values or {}).get("inputTokens")) if values else None
            if not model or values is None or input_value is None or input_value < 0:
                continue
            input_tokens = int(input_value)
            cache_read = min(max(int(_number(values.get("cachedReadTokens")) or 0), 0), input_tokens)
            cache_write = min(
                max(int(_number(values.get("cacheCreationTokens")) or 0), 0),
                input_tokens - cache_read,
            )
            output_tokens = max(int(_number(values.get("outputTokens")) or 0), 0)
            ticks = _number(values.get("costUsdTicks"))
            if ticks is None and len(model_usage) == 1:
                ticks = top_level_ticks
            carried = None
            if ticks is not None and ticks >= 0:
                carried = ticks / COST_TICKS_PER_DOLLAR
            entries.append(
                GrokEntry(
                    timestamp=timestamp,
                    model=model,
                    input_tokens=input_tokens - cache_read - cache_write,
                    cache_write_tokens=cache_write,
                    cache_read_tokens=cache_read,
                    output_tokens=output_tokens,
                    event_id=event_id,
                    carried_cost=carried,
                )
            )
        return entries

    @staticmethod
    def _timestamp(object_: Dict[str, Any], params: Optional[Dict[str, Any]]) -> Optional[datetime]:
        for metadata in ((params or {}).get("_meta"), object_.get("_meta")):
            values = _object(metadata)
            if not values:
                continue
            milliseconds = _number(values.get("agentTimestampMs"))
            if milliseconds is not None and milliseconds > 0:
                return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)

        seconds = _number(object_.get("timestamp")) if not isinstance(object_.get("timestamp"), str) else None
        if seconds is not None and seconds > 0:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        raw = object_.get("timestamp")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return None

    @staticmethod
    def dedup(entries: List[GrokEntry]) -> List[GrokEntry]:
        seen: Set[str] = set()
        kept: List[GrokEntry] = []
        for entry in entries:
            if not entry.event_id:
                kept.append(entry)
                continue
            key = f"{entry.event_id}\0{entry.model}"
            if key in seen:
                continue
            seen.add(key)
            kept.append(entry)
        return kept

    @classmethod
    def aggregate(
        cls,
        entries: List[GrokEntry],
        since: datetime,
        pricing_store: Optional[ModelPricingStore] = None,
    ) -> ProviderUsageHistory:
        pricing_store = pricing_store or ModelPricingStore.get_shared()
        daily: Dict[str, Dict[str, Any]] = {}
        models: Dict[str, Dict[str, Any]] = {}
        daily_models: Dict[str, Dict[str, Dict[str, Any]]] = {}
        unknown_models_by_day: Dict[str, Set[str]] = {}

        for entry in entries:
            if entry.timestamp < since:
                continue
            day = entry.timestamp.astimezone().date().isoformat()
            cost = entry.carried_cost
            if cost is None:
                rates = cls._known_rates(pricing_store, entry.model)
                if rates is None:
                    if entry.total_tokens > 0:
                        unknown_models_by_day.setdefault(day, set()).add(entry.model)
                    continue
                cost = rates.cost_dollars(
                    input_tokens=entry.prompt_tokens,
                    cached_tokens=entry.cache_read_tokens,
                    output_tokens=entry.output_tokens,
                    apply_long_context=False,
                )

            bucket = daily.setdefault(day, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0})
            bucket["input"] += entry.prompt_tokens
            bucket["cached"] += entry.cache_read_tokens
            bucket["output"] += entry.output_tokens
            bucket["total"] += entry.total_tokens
            bucket["cost"] += cost

            model_bucket = models.setdefault(entry.model, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0})
            model_bucket["input"] += entry.prompt_tokens
            model_bucket["cached"] += entry.cache_read_tokens
            model_bucket["output"] += entry.output_tokens
            model_bucket["total"] += entry.total_tokens
            model_bucket["cost"] += cost
            day_model = daily_models.setdefault(day, {}).setdefault(
                entry.model, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0}
            )
            day_model["input"] += entry.prompt_tokens
            day_model["cached"] += entry.cache_read_tokens
            day_model["output"] += entry.output_tokens
            day_model["total"] += entry.total_tokens
            day_model["cost"] += cost

        series = [
            DailyUsageSeries(
                date=day,
                input_tokens=values["input"],
                cached_tokens=values["cached"],
                output_tokens=values["output"],
                total_tokens=values["total"],
                estimated_cost=values["cost"],
                models=model_summaries_from_buckets(daily_models.get(day, {})),
            )
            for day, values in sorted(daily.items())
        ]
        return ProviderUsageHistory(
            series=series,
            model_usage=model_summaries_from_buckets(models),
            unknown_models_by_day={day: sorted(names) for day, names in unknown_models_by_day.items()},
        )

    @staticmethod
    def _known_rates(store: ModelPricingStore, model: str) -> Optional[ModelRates]:
        canonical = store.supplement.canonical_name(model) or model
        exact = store.catalog.find_exact(canonical)
        if exact:
            return store._with_fast_multiplier(exact[1], canonical)
        fuzzy = store.catalog.find_fuzzy(canonical)
        if fuzzy:
            return store._with_fast_multiplier(fuzzy[1], canonical)
        return None
