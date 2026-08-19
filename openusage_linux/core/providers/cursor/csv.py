"""Cursor usage CSV export parsing (port of CursorUsageCSV / CursorCSVParser)."""

from __future__ import annotations
import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openusage_linux.core.base import (
    DailyUsageSeries,
    ModelUsageSummary,
    ProviderUsageHistory,
    model_summaries_from_buckets,
)
from openusage_linux.core.pricing import ModelPricingStore

REQUIRED_COLUMNS = [
    "Date",
    "Model",
    "Input (w/ Cache Write)",
    "Input (w/o Cache Write)",
    "Cache Read",
    "Output Tokens",
]

_INT_RE = re.compile(r"^\d{1,3}(,\d{3})+$|^\d+$")


@dataclass
class CursorCSVRow:
    timestamp: datetime
    model: str
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost: float


def _parse_int(value: str) -> Optional[int]:
    trimmed = value.strip()
    if not trimmed:
        return 0
    if not _INT_RE.match(trimmed):
        return None
    try:
        parsed = int(trimmed.replace(",", ""))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_date(value: str) -> Optional[datetime]:
    trimmed = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(trimmed, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def parse_usage_csv(text: str, pricing_store: Optional[ModelPricingStore] = None) -> Optional[List[CursorCSVRow]]:
    """Parse the export; malformed rows are skipped, structural damage returns None."""
    if not text or not text.strip():
        return None
    reader = csv.reader(io.StringIO(text))
    try:
        rows = list(reader)
    except csv.Error:
        return None
    if not rows:
        return None

    header = [cell.strip().lstrip("\ufeff") for cell in rows[0]]
    if len(set(header)) != len(header):
        return None
    try:
        indices = {name: header.index(name) for name in REQUIRED_COLUMNS}
    except ValueError:
        return None

    pricing_store = pricing_store or ModelPricingStore.get_shared()
    parsed: List[CursorCSVRow] = []
    for row in rows[1:]:
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            continue  # width-mismatched rows are rejected, not fatal

        timestamp = _parse_date(row[indices["Date"]])
        if timestamp is None:
            continue
        counts = []
        ok = True
        for column in REQUIRED_COLUMNS[2:]:
            value = _parse_int(row[indices[column]])
            if value is None:
                ok = False
                break
            counts.append(value)
        if not ok:
            continue
        cache_write, input_wo, cache_read, output = counts

        model = row[indices["Model"]].strip()
        rates = pricing_store.rate_for(model) if model else None
        if rates is not None:
            cost = rates.cost_dollars(
                input_tokens=input_wo + cache_write + cache_read,
                cached_tokens=cache_read,
                output_tokens=output,
                apply_long_context=False,
            )
        else:
            cost = 0.0

        parsed.append(CursorCSVRow(
            timestamp=timestamp,
            model=model,
            input_tokens=input_wo + cache_write,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
            output_tokens=output,
            cost=cost,
        ))
    return parsed


def build_history(rows: List[CursorCSVRow]) -> ProviderUsageHistory:
    daily: Dict[str, Dict[str, Any]] = {}
    models: Dict[str, Dict[str, Any]] = {}
    daily_models: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for row in rows:
        day = row.timestamp.astimezone().date().isoformat()
        bucket = daily.setdefault(day, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0})
        bucket["input"] += row.input_tokens
        bucket["cached"] += row.cache_read_tokens
        bucket["output"] += row.output_tokens
        bucket["total"] += row.input_tokens + row.cache_read_tokens + row.output_tokens
        bucket["cost"] += row.cost

        model_key = row.model or "Unattributed"
        model_bucket = models.setdefault(model_key, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0})
        model_bucket["input"] += row.input_tokens
        model_bucket["cached"] += row.cache_read_tokens
        model_bucket["output"] += row.output_tokens
        model_bucket["total"] += row.input_tokens + row.cache_read_tokens + row.output_tokens
        model_bucket["cost"] += row.cost
        day_model = daily_models.setdefault(day, {}).setdefault(
            model_key, {"input": 0, "cached": 0, "output": 0, "total": 0, "cost": 0.0}
        )
        day_model["input"] += row.input_tokens
        day_model["cached"] += row.cache_read_tokens
        day_model["output"] += row.output_tokens
        day_model["total"] += row.input_tokens + row.cache_read_tokens + row.output_tokens
        day_model["cost"] += row.cost

    series = [
        DailyUsageSeries(
            date=day,
            input_tokens=values["input"],
            cached_tokens=values["cached"],
            output_tokens=values["output"],
            total_tokens=values["total"],
            estimated_cost=round(values["cost"], 2),
            models=model_summaries_from_buckets(daily_models.get(day, {})),
        )
        for day, values in sorted(daily.items())
    ]
    model_usage = [
        ModelUsageSummary(
            model=summary.model,
            input_tokens=summary.input_tokens,
            cached_tokens=summary.cached_tokens,
            output_tokens=summary.output_tokens,
            total_tokens=summary.total_tokens,
            estimated_cost=round(summary.estimated_cost, 2),
        )
        for summary in model_summaries_from_buckets(models)
    ]
    return ProviderUsageHistory(series=series, model_usage=model_usage)
