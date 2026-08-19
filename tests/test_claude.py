"""Fixture tests for the Claude provider (payload shapes ported from upstream)."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone

from openusage_linux.core.base import MetricFormat
from openusage_linux.core.providers.claude.auth import (
    ClaudeOAuth,
    load_candidates,
    needs_refresh,
)
from openusage_linux.core.providers.claude.mapper import format_plan, map_usage
from openusage_linux.core.providers.claude.scanner import ClaudeLogUsageScanner

USAGE_BODY = {
    "five_hour": {"utilization": 42.0, "resets_at": "2026-08-18T12:00:00Z"},
    "seven_day": {"utilization": 68.5, "resets_at": 1818880000},  # epoch seconds
    "seven_day_sonnet": {"utilization": 10.0, "resets_at": 1818880000000},  # epoch ms
    "limits": [
        {"kind": "other", "percent": 99.0},
        {
            "kind": "weekly_scoped",
            "percent": 25.0,
            "resets_at": "2026-08-20T00:00:00Z",
            "scope": {"model": {"display_name": "Fable"}},
        },
    ],
    "extra_usage": {"is_enabled": True, "used_credits": 1234, "monthly_limit": 20000},
}


class TestClaudeMapper(unittest.TestCase):
    def test_maps_all_windows(self):
        lines = map_usage(USAGE_BODY)
        labels = [line.label for line in lines]
        self.assertEqual(labels, ["Session", "Weekly", "Sonnet", "Fable", "Extra usage spent"])

        session, weekly, sonnet, fable, extra = lines
        self.assertEqual(session.used, 42.0)
        self.assertEqual(session.period_duration_ms, 5 * 3600 * 1000)
        self.assertEqual(weekly.used, 68.5)
        self.assertEqual(weekly.period_duration_ms, 7 * 86400 * 1000)
        self.assertEqual(sonnet.resets_at, datetime.fromtimestamp(1818880000, tz=timezone.utc))
        self.assertEqual(fable.used, 25.0)
        self.assertEqual(extra.format, MetricFormat.DOLLARS)
        self.assertAlmostEqual(extra.used, 12.34)
        self.assertAlmostEqual(extra.limit, 200.0)

    def test_skips_missing_windows(self):
        lines = map_usage({"five_hour": {"utilization": 5.0}})
        self.assertEqual([line.label for line in lines], ["Session"])

    def test_extra_usage_value_row_without_cap(self):
        lines = map_usage({"extra_usage": {"is_enabled": True, "used_credits": 500}})
        self.assertEqual(lines[0].kind, "values")
        self.assertAlmostEqual(lines[0].values[0].number, 5.0)

    def test_format_plan(self):
        self.assertEqual(format_plan("max", "default_20x"), "Max 20x")
        self.assertEqual(format_plan("pro", None), "Pro")
        self.assertIsNone(format_plan(None, "20x"))


class TestClaudeAuth(unittest.TestCase):
    def test_load_file_candidate_and_refresh_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            try:
                creds = {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-token",
                        "refreshToken": "refresh",
                        "expiresAt": time.time() * 1000 + 60_000,  # 1 min left
                        "subscriptionType": "max",
                        "scopes": ["user:profile", "user:inference"],
                    },
                    "otherKey": "preserved",
                }
                with open(os.path.join(tmp, ".credentials.json"), "w") as f:
                    json.dump(creds, f)

                candidates = load_candidates()
                self.assertEqual(len(candidates), 1)
                state = candidates[0]
                self.assertEqual(state.oauth.access_token, "sk-ant-token")
                self.assertEqual(state.oauth.live_usage_available(), "available")
                self.assertTrue(needs_refresh(state.oauth))  # inside 5-min margin

                state.oauth.expires_at_ms = time.time() * 1000 + 3600_000
                self.assertFalse(needs_refresh(state.oauth))
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]

    def test_env_token_is_inference_only_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "env-token"
            try:
                candidates = load_candidates()
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].source, "environment")
                self.assertEqual(
                    candidates[0].oauth.live_usage_available(inference_only=True),
                    "inference_only",
                )
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]
                del os.environ["CLAUDE_CODE_OAUTH_TOKEN"]

    def test_missing_profile_scope(self):
        oauth = ClaudeOAuth(access_token="t", scopes=["user:inference"])
        self.assertEqual(oauth.live_usage_available(), "missing_profile_scope")

    def test_comma_separated_config_dir_loads_first_existing_file(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            creds = {
                "claudeAiOauth": {
                    "accessToken": "from-second",
                    "scopes": ["user:profile"],
                }
            }
            with open(os.path.join(second, ".credentials.json"), "w") as handle:
                json.dump(creds, handle)
            os.environ["CLAUDE_CONFIG_DIR"] = f"{first},{second}"
            try:
                candidates = load_candidates()
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].oauth.access_token, "from-second")
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]


TRANSCRIPT_LINE = {
    "timestamp": "2026-08-17T10:00:00.000Z",
    "version": "2.1.69",
    "sessionId": "sess-1",
    "requestId": "req-1",
    "message": {
        "id": "msg-1",
        "model": "claude-sonnet-4-5",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 25,
            "cache_creation_input_tokens": 10,
        },
    },
    "costUSD": 0.002,
}


class TestClaudeScanner(unittest.TestCase):
    def _scan(self, lines_by_file):
        with tempfile.TemporaryDirectory() as tmp:
            projects = os.path.join(tmp, "projects", "demo")
            os.makedirs(projects)
            for name, rows in lines_by_file.items():
                with open(os.path.join(projects, name), "w") as f:
                    for row in rows:
                        f.write(json.dumps(row) + "\n")
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            try:
                scanner = ClaudeLogUsageScanner()
                return scanner.scan(days_back=300)
            finally:
                if old is None:
                    del os.environ["CLAUDE_CONFIG_DIR"]
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_parses_entries_and_dedups(self):
        duplicate = dict(TRANSCRIPT_LINE)  # same message/request id → dedup keeps one
        history = self._scan({"a.jsonl": [TRANSCRIPT_LINE, duplicate]})
        self.assertEqual(len(history.series), 1)
        day = history.series[0]
        self.assertEqual(day.total_tokens, 100 + 10 + 25 + 50)
        self.assertAlmostEqual(day.estimated_cost, 0.002)
        self.assertEqual(history.model_usage[0].model, "claude-sonnet-4-5")

    def test_rejects_invalid_lines(self):
        bad_version = dict(TRANSCRIPT_LINE, version="not-semver")
        empty_session = dict(TRANSCRIPT_LINE, sessionId="")
        history = self._scan({"a.jsonl": [bad_version, empty_session]})
        self.assertIsNotNone(history)
        self.assertEqual(history.series, [])

    def test_advisor_iterations_become_entries(self):
        line = json.loads(json.dumps(TRANSCRIPT_LINE))
        line["message"]["usage"]["iterations"] = [
            {
                "type": "advisor_message",
                "model": "claude-haiku",
                "usage": {"input_tokens": 5, "output_tokens": 7},
            }
        ]
        history = self._scan({"a.jsonl": [line]})
        models = {m.model for m in history.model_usage}
        self.assertIn("claude-haiku", models)
        self.assertEqual(history.series[0].total_tokens, 185 + 12)


if __name__ == "__main__":
    unittest.main()
