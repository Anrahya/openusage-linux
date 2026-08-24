"""Grok credits-config mapper (port of GrokUsageMapper)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from openusage_linux.core.base import MetricFormat, MetricLine

WEEKLY_PERIOD_TYPE = "USAGE_PERIOD_TYPE_WEEKLY"


class GrokUsageError(Exception):
    pass


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GrokUsageError("Grok billing response changed.")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise GrokUsageError("Grok billing response changed.")
    return number


def _parse_period_date(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GrokUsageError("Grok billing response changed.")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise GrokUsageError("Grok billing response changed.") from error
    return parsed


def _on_demand_cap(config: Dict[str, Any]) -> float:
    if "onDemandCap" not in config:
        return 0.0
    cap = config["onDemandCap"]
    if not isinstance(cap, dict):
        raise GrokUsageError("Grok billing response changed.")
    return _finite_number(cap.get("val", 0))


def map_credits_config(body: Dict[str, Any]) -> List[MetricLine]:
    if not isinstance(body, dict) or not isinstance(body.get("config"), dict):
        raise GrokUsageError("Grok billing response changed.")
    config = body["config"]
    period = config.get("currentPeriod")
    if not isinstance(period, dict):
        raise GrokUsageError("Grok billing response changed.")
    period_type = period.get("type")
    if not isinstance(period_type, str) or not period_type.strip():
        raise GrokUsageError("Grok billing response changed.")
    start = _parse_period_date(period.get("start"))
    end = _parse_period_date(period.get("end"))
    if end <= start:
        raise GrokUsageError("Grok billing response changed.")
    if "creditUsagePercent" in config:
        used = max(0.0, min(100.0, _finite_number(config["creditUsagePercent"])))
    else:
        used = 0.0
    lines: List[MetricLine] = []
    if period_type == WEEKLY_PERIOD_TYPE:
        lines.append(
            MetricLine.progress(
                label="Weekly limit",
                used=used,
                limit=100,
                format=MetricFormat.PERCENT,
                resets_at=end,
                period_duration_ms=int((end - start).total_seconds() * 1000),
            )
        )
    cap_val = _on_demand_cap(config)
    if cap_val == int(cap_val):
        cap_text = f"{int(cap_val)} cap"
    else:
        cap_text = f"{cap_val} cap"
    lines.append(
        MetricLine(
            kind="badge",
            label="Pay as you go",
            note="Disabled" if cap_val <= 0 else cap_text,
        )
    )
    return lines


def plan_name(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    plan = body.get("subscription_tier_display")
    if not isinstance(plan, str):
        return None
    trimmed = plan.strip()
    return trimmed or None
