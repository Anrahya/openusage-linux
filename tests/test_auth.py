"""Unit tests for Codex authentication store and JWT handling."""

import base64
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from openusage_linux.core.providers.codex.auth import (
    CodexAuth,
    CodexAuthState,
    CodexAuthStore,
    CodexTokens,
)


class TestCodexAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_file = Path(self.temp_dir.name) / "auth.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_dummy_jwt(self, exp_timestamp: float) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode("utf-8").rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({"exp": exp_timestamp}).encode("utf-8")).decode("utf-8").rstrip("=")
        return f"{header}.{payload}.signature"

    def test_jwt_expiry_extraction(self):
        target_exp = time.time() + 3600
        jwt_token = self._make_dummy_jwt(target_exp)
        extracted = CodexAuthStore.get_jwt_expiry(jwt_token)
        self.assertIsNotNone(extracted)
        self.assertAlmostEqual(extracted, target_exp, places=1)

    def test_needs_refresh_with_expiring_jwt(self):
        store = CodexAuthStore(codex_home=self.temp_dir.name)
        # Token expiring in 2 minutes (less than 5 min refresh window)
        expiring_jwt = self._make_dummy_jwt(time.time() + 120)
        auth = CodexAuth(tokens=CodexTokens(access_token=expiring_jwt))
        self.assertTrue(store.needs_refresh(auth))

        # Token expiring in 2 hours (more than 5 min window)
        valid_jwt = self._make_dummy_jwt(time.time() + 7200)
        auth_valid = CodexAuth(tokens=CodexTokens(access_token=valid_jwt))
        self.assertFalse(store.needs_refresh(auth_valid))

    def test_save_and_load_auth_permissions(self):
        store = CodexAuthStore(codex_home=self.temp_dir.name)
        state = CodexAuthState(
            auth=CodexAuth(
                tokens=CodexTokens(
                    access_token="test-access-token",
                    refresh_token="test-refresh-token",
                    account_id="test-account-123",
                ),
                last_refresh="2026-08-16T12:00:00Z",
            ),
            file_path=str(self.auth_file),
        )

        store.save_auth(state)
        self.assertTrue(self.auth_file.exists())

        # Verify 0600 file mode
        file_mode = stat.S_IMODE(os.stat(self.auth_file).st_mode)
        self.assertEqual(file_mode, 0o600)

        # Reload and check fields
        loaded = store.load_auth(str(self.auth_file))
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.auth.tokens.access_token, "test-access-token")
        self.assertEqual(loaded.auth.tokens.refresh_token, "test-refresh-token")
        self.assertEqual(loaded.auth.tokens.account_id, "test-account-123")

    def test_non_string_tokens_are_ignored(self):
        self.auth_file.write_text(json.dumps({"tokens": {"access_token": 12345}}), encoding="utf-8")
        store = CodexAuthStore(codex_home=self.temp_dir.name)
        loaded = store.load_auth(str(self.auth_file))
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.has_usable_access_token)
        self.assertFalse(store.needs_refresh(loaded.auth))

    def test_save_auth_preserves_unknown_keys(self):
        self.auth_file.write_text(
            json.dumps({
                "tokens": {"access_token": "old", "refresh_token": "refresh"},
                "custom_flag": True,
            }),
            encoding="utf-8",
        )
        store = CodexAuthStore(codex_home=self.temp_dir.name)
        loaded = store.load_auth(str(self.auth_file))
        loaded.auth.tokens.access_token = "new"
        store.save_auth(loaded)
        saved = json.loads(self.auth_file.read_text(encoding="utf-8"))
        self.assertTrue(saved["custom_flag"])
        self.assertEqual(saved["tokens"]["access_token"], "new")


if __name__ == "__main__":
    unittest.main()
