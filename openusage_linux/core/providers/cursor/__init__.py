"""Cursor Provider Runtime for OpenUsage Linux."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

from openusage_linux.core.base import Provider, ProviderLink, ProviderSnapshot
from openusage_linux.core.providers.cursor.auth import (
    load_auth_state,
    needs_refresh,
    save_access_token,
    user_id_from_token,
)
from openusage_linux.core.providers.cursor.client import (
    CursorClientError,
    fetch_credit_grants,
    fetch_current_period_usage,
    fetch_plan_info,
    fetch_stripe_balance,
    fetch_usage_api,
    fetch_usage_csv,
    refresh_access_token,
    session_token,
)
from openusage_linux.core.providers.cursor.csv import build_history, parse_usage_csv
from openusage_linux.core.providers.cursor.mapper import (
    CursorMapperError,
    map_request_based,
    map_usage,
)

ERROR_NOT_LOGGED_IN = "Not logged in. Sign in via Cursor app or run `agent login`."
ERROR_TOKEN_EXPIRED = "Token expired. Sign in via Cursor app or run `agent login`."


class CursorProvider:
    def __init__(self):
        self.provider = Provider(
            id="cursor",
            display_name="Cursor",
            icon_name="cursor",
            links=[
                ProviderLink(label="Status", url="https://status.cursor.com/"),
                ProviderLink(label="Dashboard", url="https://www.cursor.com/dashboard"),
            ],
        )

    def has_local_credentials(self) -> bool:
        return load_auth_state() is not None

    def refresh(self) -> ProviderSnapshot:
        now_dt = datetime.now(timezone.utc)
        state = load_auth_state()
        if not state:
            return ProviderSnapshot.error_snapshot(self.provider, ERROR_NOT_LOGGED_IN)

        # Best-effort proactive refresh; keep the old token if it fails.
        if needs_refresh(state.access_token) and state.refresh_token:
            try:
                new_token, _ = refresh_access_token(state.refresh_token)
                if new_token:
                    save_access_token(state, new_token)
            except CursorClientError as e:
                if not state.access_token:
                    return ProviderSnapshot.error_snapshot(self.provider, str(e))

        token = state.access_token
        if not token:
            return ProviderSnapshot.error_snapshot(self.provider, ERROR_NOT_LOGGED_IN)

        try:
            usage_body = fetch_current_period_usage(token)
        except CursorClientError as e:
            if e.status_code not in (401, 403):
                return ProviderSnapshot.error_snapshot(
                    self.provider, f"Usage request failed (HTTP {e.status_code}). Try again later."
                    if e.status_code else "Usage request failed. Check your connection."
                )
            if not state.refresh_token:
                return ProviderSnapshot.error_snapshot(self.provider, ERROR_TOKEN_EXPIRED)
            try:
                new_token, _ = refresh_access_token(state.refresh_token)
            except CursorClientError as refresh_error:
                return ProviderSnapshot.error_snapshot(self.provider, str(refresh_error))
            if not new_token:
                return ProviderSnapshot.error_snapshot(self.provider, ERROR_TOKEN_EXPIRED)
            save_access_token(state, new_token)
            token = new_token
            try:
                usage_body = fetch_current_period_usage(token)
            except CursorClientError as retry_error:
                return ProviderSnapshot.error_snapshot(
                    self.provider,
                    "Usage request failed after refresh. Try again."
                    if retry_error.status_code in (401, 403) else str(retry_error),
                )

        plan_name = fetch_plan_info(token)

        # Optional enrichments — all nonfatal.
        credit_grants = fetch_credit_grants(token)
        user_id = user_id_from_token(token)
        cookie = session_token(token, user_id) if user_id else None
        stripe_cents = fetch_stripe_balance(cookie) if cookie else 0

        try:
            plan, lines = map_usage(usage_body, plan_name, credit_grants, stripe_cents)
        except CursorMapperError as e:
            if not (e.fallback_allowed and cookie):
                return ProviderSnapshot.error_snapshot(self.provider, str(e))
            usage_api = fetch_usage_api(cookie, user_id)
            if not usage_api:
                return ProviderSnapshot.error_snapshot(self.provider, str(e))
            try:
                plan, lines = map_request_based(usage_api, plan_name)
            except CursorMapperError as fallback_error:
                return ProviderSnapshot.error_snapshot(self.provider, str(fallback_error))

        history = None
        if cookie:
            history = self._scan_csv(cookie, now_dt)

        return ProviderSnapshot(
            provider=self.provider,
            plan=plan,
            account_id=user_id,
            lines=lines,
            refreshed_at=now_dt,
            usage_history=history,
        )

    @staticmethod
    def _scan_csv(cookie: str, now_dt: datetime):
        start_of_day = now_dt.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int((start_of_day - timedelta(days=29)).timestamp() * 1000)
        end_ms = int(now_dt.timestamp() * 1000)
        text = fetch_usage_csv(cookie, start_ms, end_ms)
        if text is None:
            return None
        rows = parse_usage_csv(text)
        if rows is None:
            return None
        return build_history(rows)
