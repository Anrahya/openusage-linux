"""Claude Provider Runtime for OpenUsage Linux."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional

from openusage_linux.core.base import MetricLine, Provider, ProviderLink, ProviderSnapshot
from openusage_linux.core.providers.claude.auth import (
    ClaudeAuthError,
    ClaudeAuthState,
    load_candidates,
    needs_refresh,
)
from openusage_linux.core.providers.claude.client import (
    ClaudeClientError,
    fetch_usage,
    refresh_access_token,
)
from openusage_linux.core.providers.claude.mapper import format_plan, map_usage
from openusage_linux.core.providers.claude.scanner import ClaudeLogUsageScanner

ERROR_NOT_LOGGED_IN = "Not logged in. Run `claude` to authenticate."
MISSING_SCOPE_WARNING = (
    "Re-login for live usage. Run `claude` and sign in again to restore "
    "session and weekly limits."
)


class ClaudeProvider:
    def __init__(self, scanner: Optional[ClaudeLogUsageScanner] = None):
        self.provider = Provider(
            id="claude",
            display_name="Claude",
            icon_name="claude",
            links=[
                ProviderLink(label="Status", url="https://status.anthropic.com/"),
                ProviderLink(label="Dashboard", url="https://claude.ai/settings/usage"),
            ],
        )
        self.scanner = scanner or ClaudeLogUsageScanner()

    def has_local_credentials(self) -> bool:
        return any(c.oauth.has_usable_access_token for c in load_candidates())

    def refresh(self) -> ProviderSnapshot:
        candidates = load_candidates()
        if not candidates:
            return ProviderSnapshot.error_snapshot(self.provider, ERROR_NOT_LOGGED_IN)

        now_dt = datetime.now(timezone.utc)
        last_error: Optional[str] = None
        snapshot: Optional[ProviderSnapshot] = None

        for candidate in candidates:
            try:
                snapshot = self._probe(candidate, now_dt)
                break
            except ClaudeAuthError as e:
                if e.allows_fallback:
                    last_error = str(e)
                    continue
                return ProviderSnapshot.error_snapshot(self.provider, str(e))
            except ClaudeClientError as e:
                return ProviderSnapshot.error_snapshot(self.provider, str(e))
            except Exception as e:  # defensive: degrade instead of crash
                return ProviderSnapshot.error_snapshot(self.provider, f"Unexpected error: {e}")

        if snapshot is None:
            return ProviderSnapshot.error_snapshot(self.provider, last_error or ERROR_NOT_LOGGED_IN)
        return snapshot

    def _probe(self, state: ClaudeAuthState, now_dt: datetime) -> ProviderSnapshot:
        lines: List[MetricLine] = []
        warning: Optional[str] = None
        availability = state.oauth.live_usage_available(inference_only=state.source == "environment")

        if availability == "available":
            lines.extend(self._fetch_live(state))
        elif availability == "missing_profile_scope":
            warning = MISSING_SCOPE_WARNING

        # Local spend history always renders, even without live limits.
        history = None
        try:
            history = self.scanner.scan(days_back=30, now=now_dt)
        except Exception:
            pass

        if warning and not any(line.kind == "progress" for line in lines):
            lines.insert(0, MetricLine.no_data("Note", warning))
        if not lines and history is None:
            lines.append(MetricLine.no_data())

        plan = format_plan(state.oauth.subscription_type, state.oauth.rate_limit_tier)
        return ProviderSnapshot(
            provider=self.provider,
            plan=plan,
            lines=lines,
            refreshed_at=now_dt,
            usage_history=history,
        )

    def _fetch_live(self, state: ClaudeAuthState) -> List[MetricLine]:
        # Proactive refresh near expiry.
        if needs_refresh(state.oauth) and state.oauth.refresh_token:
            refresh_access_token(state)

        try:
            body, _ = fetch_usage(state.oauth.access_token or "")
        except ClaudeClientError as e:
            if e.status_code not in (401, 403):
                if e.status_code == 429:
                    minutes = self._rate_limit_minutes(e.retry_after_seconds)
                    return [MetricLine.no_data(
                        "Status", f"Rate limited, retry in ~{minutes}" if minutes else "Rate limited, try again later"
                    )]
                raise
            # One rotate-and-retry pass.
            new_token = refresh_access_token(state)
            body, _ = fetch_usage(new_token)
        return map_usage(body)

    @staticmethod
    def _rate_limit_minutes(retry_after: Optional[float]) -> Optional[str]:
        if retry_after is None:
            return None
        if retry_after <= 0:
            return "now"
        import math
        return f"{math.ceil(retry_after / 60.0)}m"
