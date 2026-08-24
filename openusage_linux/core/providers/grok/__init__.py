"""Grok Provider Runtime for OpenUsage Linux."""

from __future__ import annotations

from datetime import datetime, timezone

from openusage_linux.core.base import Provider, ProviderLink, ProviderSnapshot
from openusage_linux.core.providers.grok.auth import GrokAuthError, GrokAuthStore
from openusage_linux.core.providers.grok.client import GrokClientError, GrokUsageClient
from openusage_linux.core.providers.grok.mapper import GrokUsageError, map_credits_config, plan_name
from openusage_linux.core.providers.grok.scanner import GrokLogUsageScanner


class GrokProvider:
    def __init__(self, auth_store=None, usage_client=None, scanner=None):
        self.provider = Provider(
            id="grok",
            display_name="Grok",
            icon_name="grok",
            links=[ProviderLink(label="Usage", url="https://grok.com/?_s=usage")],
        )
        self.auth_store = auth_store or GrokAuthStore()
        self.usage_client = usage_client or GrokUsageClient()
        self.scanner = scanner or GrokLogUsageScanner()

    def has_local_credentials(self) -> bool:
        try:
            return bool(self.auth_store.load_candidates())
        except Exception:
            return False

    def refresh(self) -> ProviderSnapshot:
        try:
            candidates = self.auth_store.load_candidates()
        except Exception:
            candidates = []
        if not candidates:
            return ProviderSnapshot.error_snapshot(
                self.provider, "Grok not logged in. Run `grok login`."
            )
        state = candidates[0]
        now_dt = datetime.now(timezone.utc)
        token = state.token
        try:
            if self.auth_store.needs_refresh(state):
                token = self.auth_store.refresh_access_token(state)
            credits = self._fetch_credits(state, token)
            lines = map_credits_config(credits)
        except GrokAuthError as error:
            return ProviderSnapshot.error_snapshot(self.provider, str(error))
        except GrokClientError as error:
            return ProviderSnapshot.error_snapshot(self.provider, str(error))
        except GrokUsageError as error:
            return ProviderSnapshot.error_snapshot(self.provider, str(error))

        try:
            settings = self.usage_client.fetch_settings(state.token)
        except GrokClientError:
            settings = {}

        history = None
        try:
            history = self.scanner.scan(days_back=30, now=now_dt)
        except Exception:
            history = None

        return ProviderSnapshot(
            provider=self.provider,
            plan=plan_name(settings),
            lines=lines,
            refreshed_at=now_dt,
            usage_history=history,
        )

    def _fetch_credits(self, state, token: str):
        try:
            return self.usage_client.fetch_credits_config(token)
        except GrokClientError as error:
            if error.status_code not in (401, 403):
                raise
            token = self.auth_store.refresh_access_token(state)
            return self.usage_client.fetch_credits_config(token)
