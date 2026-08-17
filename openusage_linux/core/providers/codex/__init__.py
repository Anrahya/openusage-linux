"""Codex Provider Runtime for OpenUsage Linux."""

from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

from openusage_linux.core.base import (
    Provider,
    ProviderLink,
    ProviderSnapshot,
)
from openusage_linux.core.providers.codex.auth import (
    CodexAuthError,
    CodexAuthStore,
    CodexAuthState,
)
from openusage_linux.core.providers.codex.client import (
    CodexClientError,
    CodexUsageClient,
)
from openusage_linux.core.providers.codex.mapper import CodexUsageMapper
from openusage_linux.core.providers.codex.scanner import CodexLogUsageScanner


class CodexProvider:
    def __init__(
        self,
        auth_store: Optional[CodexAuthStore] = None,
        usage_client: Optional[CodexUsageClient] = None,
        scanner: Optional[CodexLogUsageScanner] = None,
    ):
        self.provider = Provider(
            id="codex",
            display_name="Codex",
            icon_name="codex",
            links=[
                ProviderLink(label="Status", url="https://status.openai.com/"),
                ProviderLink(label="Dashboard", url="https://chatgpt.com/codex/settings/usage"),
            ],
        )
        self.auth_store = auth_store or CodexAuthStore()
        self.usage_client = usage_client or CodexUsageClient()
        self.scanner = scanner or CodexLogUsageScanner()

    def has_local_credentials(self) -> bool:
        candidates = self.auth_store.load_auth_candidates()
        return any(c.has_usable_access_token for c in candidates)

    def refresh(self) -> ProviderSnapshot:
        candidates = self.auth_store.load_auth_candidates()
        if not candidates:
            return ProviderSnapshot.error_snapshot(
                self.provider, "No Codex authentication found. Run `codex` to log in."
            )

        last_error: Optional[str] = None
        for candidate in candidates:
            try:
                return self._probe(candidate)
            except CodexAuthError as e:
                last_error = str(e)
                continue
            except CodexClientError as e:
                return ProviderSnapshot.error_snapshot(self.provider, str(e))
            except Exception as e:
                return ProviderSnapshot.error_snapshot(self.provider, f"Unexpected error: {e}")

        return ProviderSnapshot.error_snapshot(
            self.provider, last_error or "Not logged in. Run `codex` to authenticate."
        )

    def _probe(self, auth_state: CodexAuthState) -> ProviderSnapshot:
        if not auth_state.auth.tokens or not auth_state.auth.tokens.access_token:
            if auth_state.auth.api_key:
                return ProviderSnapshot.error_snapshot(
                    self.provider, "Usage metrics are not available for API key login."
                )
            raise CodexAuthError("Not logged in. Run `codex` to log in.")

        access_token = auth_state.auth.tokens.access_token
        account_id = auth_state.auth.tokens.account_id

        # 1. Proactive Token Refresh
        if self.auth_store.needs_refresh(auth_state.auth):
            # Check if file changed on disk first
            reloaded = self.auth_store.load_auth(auth_state.file_path)
            if reloaded and reloaded.auth.tokens and reloaded.auth.tokens.access_token:
                auth_state = reloaded
                access_token = reloaded.auth.tokens.access_token
                account_id = reloaded.auth.tokens.account_id

            if self.auth_store.needs_refresh(auth_state.auth):
                access_token = self.auth_store.refresh_access_token(auth_state)

        # 2. Fetch Usage & Reset Credits
        now_dt = datetime.now(timezone.utc)
        try:
            usage_body, headers = self.usage_client.fetch_usage(
                access_token=access_token,
                account_id=account_id,
            )
        except CodexClientError as error:
            if error.status_code not in (401, 403):
                raise

            # Another Codex process may already have rotated the file. Reuse
            # that token when possible; otherwise rotate once and retry the
            # request. This keeps transient OAuth expiry from surfacing as a
            # permanent dashboard error without creating a retry loop.
            reloaded = self.auth_store.load_auth(auth_state.file_path)
            reloaded_token = (
                reloaded.auth.tokens.access_token
                if reloaded and reloaded.auth.tokens
                else None
            )
            if reloaded_token and reloaded_token != access_token:
                auth_state = reloaded
                access_token = reloaded_token
                account_id = reloaded.auth.tokens.account_id
            else:
                access_token = self.auth_store.refresh_access_token(auth_state)
                account_id = auth_state.auth.tokens.account_id if auth_state.auth.tokens else account_id

            usage_body, headers = self.usage_client.fetch_usage(
                access_token=access_token,
                account_id=account_id,
            )
        reset_credits_payload = self.usage_client.fetch_reset_credits(access_token=access_token, account_id=account_id)

        # 3. Map Usage
        plan, lines = CodexUsageMapper.map_usage(
            body=usage_body,
            headers=headers,
            reset_credits_payload=reset_credits_payload,
            now=now_dt,
        )

        account_email = usage_body.get("email")

        # 4. Scan Session Logs
        usage_history = self.scanner.scan(days_back=30)

        return ProviderSnapshot(
            provider=self.provider,
            plan=plan,
            account_email=account_email,
            account_id=account_id,
            lines=lines,
            refreshed_at=now_dt,
            usage_history=usage_history,
        )
