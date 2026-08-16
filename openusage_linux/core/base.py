"""Core data models and base classes for OpenUsage Linux."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MetricFormat(str, Enum):
    PERCENT = "percent"
    DOLLARS = "dollars"
    COUNT = "count"
    TOKENS = "tokens"


@dataclass
class MetricValue:
    number: float
    kind: MetricFormat = MetricFormat.COUNT
    label: Optional[str] = None


@dataclass
class MetricLine:
    kind: str  # 'progress', 'values', 'trend', 'no_data'
    label: str
    used: Optional[float] = None
    limit: Optional[float] = None
    format: MetricFormat = MetricFormat.PERCENT
    resets_at: Optional[datetime] = None
    period_duration_ms: Optional[int] = None
    values: List[MetricValue] = field(default_factory=list)
    expiries_at: List[datetime] = field(default_factory=list)
    note: Optional[str] = None

    @classmethod
    def progress(
        cls,
        label: str,
        used: float,
        limit: float = 100.0,
        format: MetricFormat = MetricFormat.PERCENT,
        resets_at: Optional[datetime] = None,
        period_duration_ms: Optional[int] = None,
    ) -> MetricLine:
        return cls(
            kind="progress",
            label=label,
            used=used,
            limit=limit,
            format=format,
            resets_at=resets_at,
            period_duration_ms=period_duration_ms,
        )

    @classmethod
    def values_line(
        cls,
        label: str,
        values: List[MetricValue],
        expiries_at: Optional[List[datetime]] = None,
        note: Optional[str] = None,
    ) -> MetricLine:
        return cls(
            kind="values",
            label=label,
            values=values,
            expiries_at=expiries_at or [],
            note=note,
        )

    @classmethod
    def no_data(cls, label: str = "Status", note: str = "No data available") -> MetricLine:
        return cls(kind="no_data", label=label, note=note)


@dataclass
class ProviderLink:
    label: str
    url: str


@dataclass
class Provider:
    id: str
    display_name: str
    icon_name: str
    links: List[ProviderLink] = field(default_factory=list)


@dataclass
class DailyUsageSeries:
    date: str  # YYYY-MM-DD
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


@dataclass
class ModelUsageSummary:
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


@dataclass
class ProviderUsageHistory:
    series: List[DailyUsageSeries] = field(default_factory=list)
    model_usage: List[ModelUsageSummary] = field(default_factory=list)
    unknown_models_by_day: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class ProviderSnapshot:
    provider: Provider
    plan: Optional[str] = None
    account_email: Optional[str] = None
    account_id: Optional[str] = None
    lines: List[MetricLine] = field(default_factory=list)
    refreshed_at: datetime = field(default_factory=datetime.now)
    usage_history: Optional[ProviderUsageHistory] = None
    error: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def error_snapshot(cls, provider: Provider, error_message: str) -> ProviderSnapshot:
        return cls(provider=provider, error=error_message, lines=[MetricLine.no_data("Error", error_message)])
