"""Unit tests for Codex provider orchestration and auth retry behavior."""

import unittest

from openusage_linux.core.providers.codex import CodexProvider
from openusage_linux.core.providers.codex.auth import CodexAuth, CodexAuthState, CodexTokens
from openusage_linux.core.providers.codex.client import CodexClientError


class FakeAuthStore:
    def __init__(self):
        self.state = CodexAuthState(
            auth=CodexAuth(
                tokens=CodexTokens(
                    access_token="old-access-token",
                    refresh_token="refresh-token",
                    account_id="account-123",
                )
            ),
            file_path="/tmp/openusage-test-auth.json",
        )
        self.refresh_count = 0

    def load_auth_candidates(self):
        return [self.state]

    def needs_refresh(self, auth):
        return False

    def load_auth(self, path):
        return self.state

    def refresh_access_token(self, state):
        self.refresh_count += 1
        state.auth.tokens.access_token = "new-access-token"
        return "new-access-token"


class FakeUsageClient:
    def __init__(self):
        self.access_tokens = []

    def fetch_usage(self, access_token, account_id=None):
        self.access_tokens.append(access_token)
        if len(self.access_tokens) == 1:
            raise CodexClientError("expired", status_code=401)
        return {"plan_type": "free", "email": "user@example.com"}, {}

    def fetch_reset_credits(self, access_token, account_id=None):
        return None


class FakeScanner:
    def scan(self, days_back=30):
        return None


class TestCodexProvider(unittest.TestCase):
    def test_unauthorized_usage_retries_once_after_token_rotation(self):
        auth_store = FakeAuthStore()
        usage_client = FakeUsageClient()
        provider = CodexProvider(
            auth_store=auth_store,
            usage_client=usage_client,
            scanner=FakeScanner(),
        )

        snapshot = provider.refresh()

        self.assertFalse(snapshot.is_error)
        self.assertEqual(usage_client.access_tokens, ["old-access-token", "new-access-token"])
        self.assertEqual(auth_store.refresh_count, 1)
        self.assertEqual(snapshot.account_email, "user@example.com")


if __name__ == "__main__":
    unittest.main()
