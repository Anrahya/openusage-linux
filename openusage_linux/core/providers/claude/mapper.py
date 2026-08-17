"""Claude usage mapper (port of ClaudeUsageMapper)."""

from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from openusage_linux.core.base import MetricFormat, MetricLine, MetricValue

SESSION_PERIOD_MS = 5 * 60 * 60 * 1000
WEEK_PERIOD_MS = 7 * 24 * 60 * 60 * 1000


def _float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def reset_date(value: Any) -> Optional[datetime]:
    """resets_at is polymorphic: ISO string, epoch seconds, or epoch ms."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    number = _float(value)
    if number is None:
        return None
    if abs(number) < 1e10:
        number *= 1000.0
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _window(body: Dict[str, Any], key: str, label: str, period_ms: int) -> Optional[MetricLine]:
    window = body.get(key)
    if not isinstance(window, dict):
        return None
    utilization = _float(window.get("utilization"))
    if utilization is None:
        return None
    return MetricLine.progress(
        label=label,
        used=max(0.0, min(100.0, utilization)),
        resets_at=reset_date(window.get("resets_at")),
        period_duration_ms=period_ms,
    )


def _scoped_fable(body: Dict[str, Any]) -> Optional[MetricLine]:
    limits = body.get("limits")
    if not isinstance(limits, list):
        return None
    for entry in limits:
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        scope = entry.get("scope")
        model = scope.get("model") if isinstance(scope, dict) else None
        display_name = model.get("display_name") if isinstance(model, dict) else None
        percent = _float(entry.get("percent"))
        if display_name == "Fable" and percent is not None:
            return MetricLine.progress(
                label="Fable",
                used=max(0.0, min(100.0, percent)),
                resets_at=reset_date(entry.get("resets_at")),
                period_duration_ms=WEEK_PERIOD_MS,
            )
    return None


def _extra_usage(body: Dict[str, Any]) -> Optional[MetricLine]:
    extra = body.get("extra_usage")
    if not isinstance(extra, dict) or extra.get("is_enabled") is not True:
        return None
    used_credits = _float(extra.get("used_credits"))
    if used_credits is None:
        return None
    used_dollars = used_credits / 100.0
    monthly_limit = _float(extra.get("monthly_limit"))
    if monthly_limit is not None and monthly_limit > 0:
        return MetricLine.progress(
            label="Extra usage spent",
            used=used_dollars,
            limit=monthly_limit / 100.0,
            format=MetricFormat.DOLLARS,
        )
    if used_dollars > 0:
        return MetricLine.values_line(
            label="Extra usage spent",
            values=[MetricValue(number=used_dollars, kind=MetricFormat.DOLLARS)],
        )
    return None


def map_usage(body: Dict[str, Any]) -> List[MetricLine]:
    lines: List[MetricLine] = []
    for line in (
        _window(body, "five_hour", "Session", SESSION_PERIOD_MS),
        _window(body, "seven_day", "Weekly", WEEK_PERIOD_MS),
        _window(body, "seven_day_sonnet", "Sonnet", WEEK_PERIOD_MS),
        _scoped_fable(body),
        _extra_usage(body),
    ):
        if line is not None:
            lines.append(line)
    return lines


def format_plan(subscription_type: Optional[str], rate_limit_tier: Optional[str]) -> Optional[str]:
    """Plan name comes from credentials: 'max' + tier 'default_20x' -> 'Max 20x'."""
    if not subscription_type or not str(subscription_type).strip():
        return None
    words = str(subscription_type).strip().split()
    plan = " ".join(word[:1].upper() + word[1:].lower() for word in words)
    if rate_limit_tier:
        match = re.search(r"\d+x", str(rate_limit_tier))
        if match:
            plan = f"{plan} {match.group(0)}"
    return plan
