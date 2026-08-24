"""Fixture tests for the Grok provider (payload shapes ported from upstream)."""

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timezone

from openusage_linux.core.base import MetricFormat
from openusage_linux.core.providers.grok import GrokProvider
from openusage_linux.ui.icons import provider_icon_path
from openusage_linux.core.providers.grok.auth import GrokAuthError, GrokAuthState, GrokAuthStore, load_candidates
from openusage_linux.core.providers.grok.client import GrokClientError, refresh_form_body
from openusage_linux.core.providers.grok.mapper import GrokUsageError, map_credits_config

# Live `/v1/billing?format=credits` body captured 2026-07-06 (percent edited nonzero).
CAPTURED_CREDITS = {
    "config": {
        "creditUsagePercent": 99.0,
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-06-30T21:36:52.140114+00:00",
            "end": "2026-07-07T21:36:52.140114+00:00",
        },
        "onDemandCap": {"val": 0},
        "onDemandUsed": {"val": 0},
        "isUnifiedBillingUser": True,
        "prepaidBalance": {"val": 0},
        "topUpMethod": "TOP_UP_METHOD_SAVED_PAYMENT_METHOD",
        "billingPeriodStart": "2026-06-30T21:36:52.140114+00:00",
        "billingPeriodEnd": "2026-07-07T21:36:52.140114+00:00",
    }
}


class TestGrokMapper(unittest.TestCase):
    def test_maps_weekly_line_and_disabled_badge_from_captured_response(self):
        lines = map_credits_config(CAPTURED_CREDITS)

        weekly = next(line for line in lines if line.label == "Weekly limit")
        self.assertEqual(weekly.kind, "progress")
        self.assertEqual(weekly.used, 99.0)
        self.assertEqual(weekly.limit, 100)
        self.assertEqual(weekly.format, MetricFormat.PERCENT)
        self.assertEqual(
            weekly.resets_at,
            datetime(2026, 7, 7, 21, 36, 52, 140114, tzinfo=timezone.utc),
        )
        self.assertEqual(weekly.period_duration_ms, 7 * 24 * 60 * 60 * 1000)

        badge = next(line for line in lines if line.label == "Pay as you go")
        self.assertEqual(badge.kind, "badge")
        self.assertEqual(badge.note, "Disabled")

    def test_maps_enabled_pay_as_you_go_cap(self):
        body = {
            "config": {
                "creditUsagePercent": 99.0,
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "start": "2026-06-30T21:36:52.140114+00:00",
                    "end": "2026-07-07T21:36:52.140114+00:00",
                },
                "onDemandCap": {"val": 2500},
            }
        }
        lines = map_credits_config(body)
        badge = next(line for line in lines if line.label == "Pay as you go")
        self.assertEqual(badge.note, "2500 cap")

    def test_absent_percent_maps_as_zero(self):
        body = {
            "config": {
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "start": "2026-06-30T21:36:52.140114+00:00",
                    "end": "2026-07-07T21:36:52.140114+00:00",
                }
            }
        }
        lines = map_credits_config(body)
        weekly = next(line for line in lines if line.label == "Weekly limit")
        self.assertEqual(weekly.used, 0.0)

    def test_non_weekly_period_omits_weekly_line(self):
        body = {
            "config": {
                "creditUsagePercent": 40.0,
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_MONTHLY",
                    "start": "2026-06-30T21:36:52.140114+00:00",
                    "end": "2026-07-30T21:36:52.140114+00:00",
                },
            }
        }
        lines = map_credits_config(body)
        self.assertIsNone(next((line for line in lines if line.label == "Weekly limit"), None))
        self.assertEqual(next(line for line in lines if line.label == "Pay as you go").note, "Disabled")

    def test_rejects_non_numeric_percent(self):
        body = {
            "config": {
                "creditUsagePercent": "high",
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "start": "2026-06-30T21:36:52.140114+00:00",
                    "end": "2026-07-07T21:36:52.140114+00:00",
                },
            }
        }
        with self.assertRaises(GrokUsageError):
            map_credits_config(body)


class TestGrokAuth(unittest.TestCase):
    def test_loads_candidate_from_grok_auth_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GROK_HOME"] = tmp
            try:
                with open(os.path.join(tmp, "auth.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "https://auth.x.ai::client": {
                                "key": "token",
                                "refresh_token": "refresh",
                            }
                        },
                        handle,
                    )
                candidates = load_candidates()
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].token, "token")
                self.assertEqual(candidates[0].entry_key, "https://auth.x.ai::client")
            finally:
                del os.environ["GROK_HOME"]

    def test_loads_opencode_xai_oauth_when_grok_cli_is_absent(self):
        with tempfile.TemporaryDirectory() as grok_home, tempfile.TemporaryDirectory() as oc_home:
            os.environ["GROK_HOME"] = grok_home
            os.environ["OPENCODE_DATA_DIR"] = oc_home
            try:
                conn = sqlite3.connect(os.path.join(oc_home, "opencode.db"))
                conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO credential VALUES (?, ?)",
                    (
                        "xai",
                        json.dumps(
                            {
                                "type": "oauth",
                                "methodID": "device",
                                "access": "opencode-xai-access",
                                "refresh": "opencode-xai-refresh",
                                "expires": 1787197338081,
                            }
                        ),
                    ),
                )
                conn.commit()
                conn.close()
                candidates = load_candidates()
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].token, "opencode-xai-access")
                self.assertEqual(candidates[0].source, "opencode")
            finally:
                del os.environ["GROK_HOME"]
                del os.environ["OPENCODE_DATA_DIR"]

    def test_grok_cli_auth_wins_over_opencode_xai(self):
        with tempfile.TemporaryDirectory() as grok_home, tempfile.TemporaryDirectory() as oc_home:
            os.environ["GROK_HOME"] = grok_home
            os.environ["OPENCODE_DATA_DIR"] = oc_home
            try:
                with open(os.path.join(grok_home, "auth.json"), "w", encoding="utf-8") as handle:
                    json.dump({"https://auth.x.ai::client": {"key": "cli-token"}}, handle)
                conn = sqlite3.connect(os.path.join(oc_home, "opencode.db"))
                conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO credential VALUES (?, ?)",
                    ("xai", json.dumps({"access": "opencode-token"})),
                )
                conn.commit()
                conn.close()
                candidates = load_candidates()
                self.assertEqual([c.token for c in candidates], ["cli-token"])
                self.assertEqual(candidates[0].source, "file")
            finally:
                del os.environ["GROK_HOME"]
                del os.environ["OPENCODE_DATA_DIR"]

    def test_save_rotates_token_atomically_and_keeps_sibling_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GROK_HOME"] = tmp
            try:
                path = os.path.join(tmp, "auth.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "https://auth.x.ai::client": {
                                "key": "old-token",
                                "refresh_token": "refresh",
                                "custom_field": "keep-me",
                            },
                            "https://auth.x.ai::other": {"key": "other-token"},
                        },
                        handle,
                    )
                store = GrokAuthStore()
                state = store.load_candidates()[0]
                state.token = "new-token"
                state.entry["key"] = "new-token"
                state.entry["refresh_token"] = "new-refresh"
                store.save(state)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                with open(path, encoding="utf-8") as handle:
                    saved = json.load(handle)
                self.assertEqual(saved["https://auth.x.ai::client"]["key"], "new-token")
                self.assertEqual(saved["https://auth.x.ai::client"]["refresh_token"], "new-refresh")
                self.assertEqual(saved["https://auth.x.ai::client"]["custom_field"], "keep-me")
                self.assertEqual(saved["https://auth.x.ai::other"]["key"], "other-token")
            finally:
                del os.environ["GROK_HOME"]

    def test_save_refuses_to_overwrite_a_corrupt_auth_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GROK_HOME"] = tmp
            try:
                path = os.path.join(tmp, "auth.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"https://auth.x.ai::client": {"key": "token", "refresh_token": "refresh"}}, handle)
                store = GrokAuthStore()
                state = store.load_candidates()[0]
                state.token = "rotated-token"
                state.entry["key"] = "rotated-token"
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("{ not valid json")
                with self.assertRaises(GrokAuthError):
                    store.save(state)
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "{ not valid json")
            finally:
                del os.environ["GROK_HOME"]

    def test_save_writes_opencode_xai_back_to_database_not_grok_auth(self):
        with tempfile.TemporaryDirectory() as grok_home, tempfile.TemporaryDirectory() as oc_home:
            os.environ["GROK_HOME"] = grok_home
            os.environ["OPENCODE_DATA_DIR"] = oc_home
            try:
                db_path = os.path.join(oc_home, "opencode.db")
                conn = sqlite3.connect(db_path)
                conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO credential VALUES (?, ?)",
                    (
                        "xai",
                        json.dumps(
                            {
                                "type": "oauth",
                                "access": "old-access",
                                "refresh": "old-refresh",
                                "expires": 1,
                            }
                        ),
                    ),
                )
                conn.commit()
                conn.close()
                store = GrokAuthStore()
                state = store.load_candidates()[0]
                state.token = "new-access"
                state.entry["access"] = "new-access"
                state.entry["refresh"] = "new-refresh"
                state.entry["expires"] = 99
                store.save(state)
                self.assertFalse(os.path.exists(os.path.join(grok_home, "auth.json")))
                conn = sqlite3.connect(db_path)
                raw = conn.execute("SELECT value FROM credential WHERE integration_id = 'xai'").fetchone()[0]
                conn.close()
                saved = json.loads(raw)
                self.assertEqual(saved["access"], "new-access")
                self.assertEqual(saved["refresh"], "new-refresh")
                self.assertEqual(saved["expires"], 99)
            finally:
                del os.environ["GROK_HOME"]
                del os.environ["OPENCODE_DATA_DIR"]


class FakeGrokAuthStore:
    def __init__(self, token="token"):
        self.state = GrokAuthState(
            token=token,
            entry_key="https://auth.x.ai::client",
            entry={"key": token, "refresh_token": "refresh"},
            auth={},
            file_path="/tmp/openusage-test-grok-auth.json",
        )
        self.refresh_count = 0

    def load_candidates(self):
        return [self.state]

    def needs_refresh(self, state):
        return False

    def refresh_access_token(self, state):
        self.refresh_count += 1
        state.token = "new-token"
        state.entry["key"] = "new-token"
        return "new-token"


class FakeGrokClient:
    def __init__(self, credits=None, settings=None, fail_first=False):
        self.credits = credits if credits is not None else CAPTURED_CREDITS
        self.settings = settings if settings is not None else {"subscription_tier_display": "SuperGrok"}
        self.tokens = []
        self.fail_first = fail_first

    def fetch_credits_config(self, access_token):
        self.tokens.append(access_token)
        if self.fail_first and len(self.tokens) == 1:
            raise GrokClientError("expired", status_code=401)
        return self.credits

    def fetch_settings(self, access_token):
        return self.settings


class FakeGrokScanner:
    def scan(self, days_back=30, now=None):
        return None


class TestGrokProvider(unittest.TestCase):
    def test_refresh_maps_weekly_pool_and_plan_name(self):
        provider = GrokProvider(
            auth_store=FakeGrokAuthStore(),
            usage_client=FakeGrokClient(),
            scanner=FakeGrokScanner(),
        )
        snapshot = provider.refresh()
        self.assertFalse(snapshot.is_error)
        self.assertEqual(snapshot.plan, "SuperGrok")
        self.assertEqual(snapshot.provider.id, "grok")
        weekly = next(line for line in snapshot.lines if line.label == "Weekly limit")
        self.assertEqual(weekly.used, 99.0)
        badge = next(line for line in snapshot.lines if line.label == "Pay as you go")
        self.assertEqual(badge.note, "Disabled")

    def test_credits_fetch_failure_fails_the_provider(self):
        class FailingClient:
            def fetch_credits_config(self, access_token):
                raise GrokClientError("Grok billing request failed (HTTP 503). Try again later.", status_code=503)

            def fetch_settings(self, access_token):
                raise AssertionError("settings must not be fetched after credits fail")

        snapshot = GrokProvider(
            auth_store=FakeGrokAuthStore(),
            usage_client=FailingClient(),
            scanner=FakeGrokScanner(),
        ).refresh()
        self.assertTrue(snapshot.is_error)
        self.assertIsNone(next((line for line in snapshot.lines if line.label == "Weekly limit"), None))

    def test_malformed_credits_body_fails_the_provider(self):
        snapshot = GrokProvider(
            auth_store=FakeGrokAuthStore(),
            usage_client=FakeGrokClient(credits={"config": {}}),
            scanner=FakeGrokScanner(),
        ).refresh()
        self.assertTrue(snapshot.is_error)

    def test_retries_credits_once_after_auth_error(self):
        auth_store = FakeGrokAuthStore(token="old-token")
        client = FakeGrokClient(fail_first=True)
        snapshot = GrokProvider(
            auth_store=auth_store,
            usage_client=client,
            scanner=FakeGrokScanner(),
        ).refresh()
        self.assertFalse(snapshot.is_error)
        self.assertEqual(client.tokens, ["old-token", "new-token"])
        self.assertEqual(auth_store.refresh_count, 1)
        self.assertEqual(next(line for line in snapshot.lines if line.label == "Weekly limit").used, 99.0)


class TestGrokIcon(unittest.TestCase):
    def test_ships_grok_icon(self):
        path = provider_icon_path("grok")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())


class TestGrokClient(unittest.TestCase):
    def test_refresh_form_encodes_reserved_characters(self):
        self.assertEqual(
            refresh_form_body("refresh token&=+/?%", "client id&=+/?%"),
            "grant_type=refresh_token&client_id=client%20id%26%3D%2B%2F%3F%25"
            "&refresh_token=refresh%20token%26%3D%2B%2F%3F%25",
        )
