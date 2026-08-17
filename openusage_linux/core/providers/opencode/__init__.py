"""OpenCode Provider Runtime for OpenUsage Linux."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from openusage_linux.core.base import MetricLine, Provider, ProviderLink, ProviderSnapshot
from openusage_linux.core.providers.opencode.auth import (
    OpenCodeAuthError,
    go_api_key,
    has_footprint,
)
from openusage_linux.core.providers.opencode.client import (
    OpenCodeClientError,
    error_type,
    fetch_usage,
)
from openusage_linux.core.providers.opencode.scanner import (
    OpenCodeScanError,
    has_hosted_usage,
    scan,
)

SESSION_PERIOD_MS = 5 * 60 * 60 * 1000
WEEK_PERIOD_MS = 7 * 24 * 60 * 60 * 1000
MONTH_PERIOD_MS = 30 * 24 * 60 * 60 * 1000

ERROR_NOT_LOGGED_IN = "OpenCode not detected. Log in with OpenCode Go or use OpenCode locally first."


def _parse_reset(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            normalized = value.strip().replace(" ", "T", 1).replace(" UTC", "+00:00")
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _window(window: Any, label: str, period_ms: int) -> Optional[MetricLine]:
    if not isinstance(window, dict):
        return None
    percent = window.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    used = max(0.0, min(100.0, float(percent)))
    return MetricLine.progress(
        label=label,
        used=used,
        resets_at=_parse_reset(window.get("resetsAt")),
        period_duration_ms=period_ms,
    )


def map_go_usage(body: Dict[str, Any]) -> Tuple[str, List[MetricLine]]:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise OpenCodeClientError("Usage response invalid. Try again later.")
    lines: List[MetricLine] = []
    for key, label, period in (
        ("rolling", "Session", SESSION_PERIOD_MS),
        ("weekly", "Weekly", WEEK_PERIOD_MS),
        ("monthly", "Monthly", MONTH_PERIOD_MS),
    ):
        line = _window(usage.get(key), label, period)
        if line is not None:
            lines.append(line)
    return "Go", lines


class OpenCodeProvider:
    def __init__(self):
        self.provider = Provider(
            id="opencode",
            display_name="OpenCode",
            icon_name="opencode",
            links=[ProviderLink(label="Dashboard", url="https://opencode.ai/auth")],
        )

    def has_local_credentials(self) -> bool:
        try:
            if go_api_key() is not None:
                return True
        except OpenCodeAuthError:
            return True  # broken auth.json is still an OpenCode footprint
        return has_footprint() or has_hosted_usage()

    def refresh(self) -> ProviderSnapshot:
        now_dt = datetime.now(timezone.utc)
        lines: List[MetricLine] = []
        plan: Optional[str] = None
        auth_error: Optional[str] = None

        # 1. Go plan meters (network).
        try:
            api_key = go_api_key()
        except OpenCodeAuthError as e:
            api_key = None
            auth_error = str(e)

        if api_key:
            try:
                body, _ = fetch_usage(api_key)
                plan, meter_lines = map_go_usage(body)
                lines.extend(meter_lines)
            except OpenCodeClientError as e:
                if e.status_code == 401:
                    return ProviderSnapshot.error_snapshot(
                        self.provider, "OpenCode Go key was rejected. Log into OpenCode Go again."
                    )
                if e.status_code == 403 and error_type(e.body) == "EntitlementError":
                    pass  # no Go subscription — local logs may still show usage
                elif e.status_code is not None:
                    return ProviderSnapshot.error_snapshot(
                        self.provider, f"Usage request failed (HTTP {e.status_code}). Try again later."
                    )
                else:
                    return ProviderSnapshot.error_snapshot(
                        self.provider, "Usage request failed. Check your connection."
                    )

        # 2. Local spend history (SQLite logs).
        history = None
        scan_error: Optional[str] = None
        try:
            history = scan(now=now_dt)
        except OpenCodeScanError as e:
            scan_error = str(e)

        if scan_error and not lines:
            return ProviderSnapshot.error_snapshot(self.provider, scan_error)

        if history is None and not lines:
            if api_key:
                return ProviderSnapshot.error_snapshot(
                    self.provider, "No OpenCode Go subscription on this key."
                )
            return ProviderSnapshot.error_snapshot(self.provider, auth_error or ERROR_NOT_LOGGED_IN)

        if not lines and history is not None and not history.series and not history.model_usage:
            lines.append(MetricLine.no_data("Status", "No usage data"))

        return ProviderSnapshot(
            provider=self.provider,
            plan=plan,
            lines=lines,
            refreshed_at=now_dt,
            usage_history=history,
        )
