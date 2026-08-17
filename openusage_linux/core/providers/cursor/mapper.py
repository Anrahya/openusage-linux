"""Cursor usage mappers (port of CursorUsageMapper / CursorUsageSummaryMapper)."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from openusage_linux.core.base import MetricFormat, MetricLine, MetricValue


class CursorMapperError(Exception):
    def __init__(self, message: str, fallback_allowed: bool = False):
        super().__init__(message)
        self.fallback_allowed = fallback_allowed


def _float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _title_case(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in value.split())


def parse_billing_cycle(usage_body: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[int]]:
    """GetCurrentPeriodUsage billing fields are epoch milliseconds."""
    start = _float(usage_body.get("billingCycleStart"))
    end = _float(usage_body.get("billingCycleEnd"))
    if start is not None and end is not None and end > start:
        return datetime.fromtimestamp(end / 1000.0, tz=timezone.utc), int(end - start)
    if end is not None:
        return datetime.fromtimestamp(end / 1000.0, tz=timezone.utc), None
    return None, None


def map_usage(
    usage_body: Dict[str, Any],
    plan_name: Optional[str] = None,
    credit_grants: Optional[Dict[str, Any]] = None,
    stripe_balance_cents: int = 0,
) -> Tuple[Optional[str], List[MetricLine]]:
    lines: List[MetricLine] = []

    usage = usage_body.get("usage") if isinstance(usage_body.get("usage"), dict) else usage_body
    enabled = usage.get("enabled") is not False
    plan_usage = usage.get("planUsage") if isinstance(usage.get("planUsage"), dict) else None

    if not enabled or plan_usage is None:
        raise CursorMapperError("No active Cursor subscription.")

    limit = _float(plan_usage.get("limit"))
    total_percent_used = _float(plan_usage.get("totalPercentUsed"))
    if limit is None and total_percent_used is None:
        raise CursorMapperError("Total usage limit missing from API response.")

    # 1. Credits (grants + Stripe balance), dollars remaining.
    grant_total = _float((credit_grants or {}).get("totalCents")) if credit_grants and credit_grants.get("hasCreditGrants") is True else None
    grant_used = _float((credit_grants or {}).get("usedCents")) if grant_total is not None and grant_total > 0 else None
    combined_total = (grant_total if grant_used is not None else 0.0) + stripe_balance_cents
    if combined_total > 0:
        remaining = max(0.0, combined_total - (grant_used or 0.0))
        lines.append(MetricLine.values_line(
            label="Credits",
            values=[MetricValue(number=remaining / 100.0, kind=MetricFormat.DOLLARS, label="left")],
        ))

    # 2. Total usage — team accounts meter dollars, individuals meter percent.
    total_spend = _float(plan_usage.get("totalSpend"))
    remaining_cents = _float(plan_usage.get("remaining"))
    plan_used = total_spend if total_spend is not None else (
        (limit - remaining_cents) if limit is not None and remaining_cents is not None else None
    )
    computed_percent = (plan_used / limit * 100.0) if (plan_used is not None and limit and limit > 0) else 0.0
    total_usage_percent = total_percent_used if total_percent_used is not None else computed_percent

    spend_limit = usage.get("spendLimitUsage") if isinstance(usage.get("spendLimitUsage"), dict) else {}
    limit_type = str(spend_limit.get("limitType") or "").lower()
    pooled_limit = _float(spend_limit.get("pooledLimit")) or 0.0
    normalized_plan = (plan_name or "").strip().lower()
    is_team = normalized_plan == "team" or limit_type == "team" or pooled_limit > 0

    resets_at, period_ms = parse_billing_cycle(usage)

    if is_team:
        if limit is None or plan_used is None:
            raise CursorMapperError("Cursor request-based usage data unavailable. Try again later.", fallback_allowed=True)
        lines.append(MetricLine.progress(
            "Total usage", plan_used / 100.0, limit=limit / 100.0,
            format=MetricFormat.DOLLARS, resets_at=resets_at, period_duration_ms=period_ms,
        ))
    else:
        lines.append(MetricLine.progress(
            "Total usage", total_usage_percent, resets_at=resets_at, period_duration_ms=period_ms,
        ))

    # 3. Auto / API usage percentages.
    auto_percent = _float(plan_usage.get("autoPercentUsed"))
    if auto_percent is not None:
        lines.append(MetricLine.progress("Auto usage", auto_percent))
    api_percent = _float(plan_usage.get("apiPercentUsed"))
    if api_percent is not None:
        lines.append(MetricLine.progress("API usage", api_percent))

    # 4. On-demand / extra usage.
    individual_limit = _float(spend_limit.get("individualLimit"))
    pooled = _float(spend_limit.get("pooledLimit"))
    on_demand_limit = individual_limit if individual_limit is not None else (pooled or 0.0)
    individual_remaining = _float(spend_limit.get("individualRemaining"))
    pooled_remaining = _float(spend_limit.get("pooledRemaining"))
    on_demand_remaining = individual_remaining if individual_remaining is not None else (pooled_remaining or 0.0)
    individual_used = _float(spend_limit.get("individualUsed"))
    pooled_used = _float(spend_limit.get("pooledUsed"))
    on_demand_used: Optional[float] = None
    for candidate in (individual_used, pooled_used, _float(spend_limit.get("totalSpend"))):
        if candidate is not None and candidate > 0:
            on_demand_used = candidate
            break
    if on_demand_used is None:
        inferred = max(0.0, on_demand_limit - on_demand_remaining)
        on_demand_used = inferred if inferred > 0 else (individual_used or pooled_used or 0.0)

    if on_demand_limit > 0:
        lines.append(MetricLine.progress(
            "On-demand", on_demand_used / 100.0, limit=on_demand_limit / 100.0, format=MetricFormat.DOLLARS,
        ))
    elif on_demand_used and on_demand_used > 0:
        lines.append(MetricLine.values_line(
            label="On-demand",
            values=[MetricValue(number=on_demand_used / 100.0, kind=MetricFormat.DOLLARS)],
        ))

    return plan_name, lines


def map_request_based(usage_api_body: Dict[str, Any], plan_name: Optional[str] = None) -> Tuple[Optional[str], List[MetricLine]]:
    """Generic fallback: /api/usage request counts only."""
    usage = usage_api_body.get("usage") if isinstance(usage_api_body.get("usage"), dict) else {}
    gpt4 = usage.get("gpt-4") if isinstance(usage.get("gpt-4"), dict) else None
    max_requests = _float((gpt4 or {}).get("maxRequestUsage")) or 0.0
    if max_requests <= 0:
        raise CursorMapperError("Cursor request-based usage data unavailable. Try again later.", fallback_allowed=True)
    used = _float((gpt4 or {}).get("numRequests")) or 0.0
    lines = [MetricLine.progress("Requests", used, limit=max_requests, format=MetricFormat.COUNT)]
    return plan_name, lines
