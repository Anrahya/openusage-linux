"""Fixture tests for the OpenCode provider."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone

from openusage_linux.core.providers.opencode import map_go_usage
from openusage_linux.core.providers.opencode.auth import OpenCodeAuthError, go_api_key, has_footprint
from openusage_linux.core.providers.opencode.client import error_type
from openusage_linux.core.providers.opencode.scanner import has_hosted_usage, scan
from openusage_linux.core.providers.opencode import OpenCodeProvider

GO_USAGE_BODY = {
    "usage": {
        "rolling": {"percent": 55.5, "resetsAt": "2026-08-18 06:30:00 UTC"},
        "weekly": {"percent": 20.0, "resetsAt": "2026-08-22T00:00:00Z"},
        "monthly": {"percent": 150.0},  # clamps to 100, no reset
    }
}


class TestOpenCodeMapper(unittest.TestCase):
    def test_maps_go_meters(self):
        plan, lines = map_go_usage(GO_USAGE_BODY)
        self.assertEqual(plan, "Go")
        self.assertEqual([line.label for line in lines], ["Session", "Weekly", "Monthly"])
        self.assertEqual(lines[0].used, 55.5)
        self.assertEqual(lines[0].period_duration_ms, 5 * 3600 * 1000)
        self.assertEqual(lines[1].period_duration_ms, 7 * 86400 * 1000)
        self.assertEqual(lines[2].used, 100.0)  # clamped
        self.assertIsNone(lines[2].resets_at)
        self.assertEqual(
            lines[0].resets_at,
            datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc),
        )

    def test_invalid_body_raises(self):
        from openusage_linux.core.providers.opencode.client import OpenCodeClientError
        with self.assertRaises(OpenCodeClientError):
            map_go_usage({"nope": True})

    def test_error_type(self):
        self.assertEqual(error_type('{"error": {"type": "EntitlementError"}}'), "EntitlementError")
        self.assertIsNone(error_type("<html>cloudflare</html>"))


class TestOpenCodeAuth(unittest.TestCase):
    def test_reads_go_key_and_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENCODE_DATA_DIR"] = tmp
            try:
                with open(os.path.join(tmp, "auth.json"), "w") as f:
                    json.dump({"opencode-go": {"key": " key-123 "}, "other": {}}, f)
                self.assertEqual(go_api_key(), "key-123")

                with open(os.path.join(tmp, "auth.json"), "w") as f:
                    f.write("{broken")
                with self.assertRaises(OpenCodeAuthError):
                    go_api_key()
            finally:
                del os.environ["OPENCODE_DATA_DIR"]

    def test_auth_json_wins_over_database_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENCODE_DATA_DIR"] = tmp
            try:
                with open(os.path.join(tmp, "auth.json"), "w", encoding="utf-8") as handle:
                    json.dump({"opencode-go": {"key": "from-file"}}, handle)
                conn = sqlite3.connect(os.path.join(tmp, "opencode.db"))
                conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO credential VALUES (?, ?)",
                    ("opencode-go", json.dumps({"type": "api", "key": "from-db"})),
                )
                conn.commit()
                conn.close()
                self.assertEqual(go_api_key(), "from-file")
            finally:
                del os.environ["OPENCODE_DATA_DIR"]

    def test_missing_file_is_logout_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENCODE_DATA_DIR"] = tmp
            try:
                self.assertIsNone(go_api_key())
            finally:
                del os.environ["OPENCODE_DATA_DIR"]


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE message (time_created INTEGER, data TEXT)")
    for created, data in rows:
        conn.execute("INSERT INTO message VALUES (?, ?)", (created, json.dumps(data)))
    conn.commit()
    conn.close()


class TestOpenCodeScanner(unittest.TestCase):
    def test_scans_hosted_messages(self):
        now_ms = int(time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENCODE_DATA_DIR"] = tmp
            try:
                _make_db(os.path.join(tmp, "opencode.db"), [
                    (now_ms - 3600_000, {
                        "role": "assistant", "providerID": "opencode-go",
                        "modelID": "gpt-5-nano", "cost": 0.05,
                        "tokens": {"total": 1500},
                    }),
                    (now_ms - 7200_000, {
                        "role": "assistant", "providerID": "anthropic",  # BYO key: excluded
                        "modelID": "claude", "cost": 0.10, "tokens": {"total": 999},
                    }),
                    (now_ms - 1800_000, {
                        "role": "user", "providerID": "opencode-go",  # wrong role
                        "cost": 1.0, "tokens": {"total": 1},
                    }),
                ])
                self.assertTrue(has_hosted_usage())
                history = scan(days_back=30)
                self.assertEqual(len(history.series), 1)
                day = history.series[0]
                self.assertEqual(day.total_tokens, 1500)
                self.assertAlmostEqual(day.estimated_cost, 0.05)
                self.assertEqual(history.model_usage[0].model, "gpt-5-nano")
            finally:
                del os.environ["OPENCODE_DATA_DIR"]

    def test_no_databases_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENCODE_DATA_DIR"] = tmp
            try:
                self.assertIsNone(scan())
                self.assertFalse(has_hosted_usage())
            finally:
                del os.environ["OPENCODE_DATA_DIR"]

    def test_session_message_schema_and_db_key(self):
        now_ms = int(time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENCODE_DATA_DIR"] = tmp
            try:
                conn = sqlite3.connect(os.path.join(tmp, "opencode.db"))
                conn.execute("CREATE TABLE session_message (type TEXT, time_created INTEGER, data TEXT)")
                conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO session_message VALUES (?, ?, ?)",
                    (
                        "assistant",
                        now_ms - 1800_000,
                        json.dumps({
                            "cost": 0.12,
                            "tokens": {"input": 100, "output": 20, "reasoning": 5},
                            "model": {"id": "muse-spark", "providerID": "opencode-go"},
                        }),
                    ),
                )
                conn.execute(
                    "INSERT INTO credential VALUES (?, ?)",
                    ("opencode-go", json.dumps({"type": "api", "key": " go-from-db "})),
                )
                conn.commit()
                conn.close()

                self.assertTrue(has_footprint())
                self.assertTrue(has_hosted_usage())
                self.assertEqual(go_api_key(), "go-from-db")
                self.assertTrue(OpenCodeProvider().has_local_credentials())
                history = scan(days_back=30)
                self.assertEqual(history.series[0].total_tokens, 125)
                self.assertAlmostEqual(history.series[0].estimated_cost, 0.12)
                self.assertEqual(history.model_usage[0].model, "muse-spark")
            finally:
                del os.environ["OPENCODE_DATA_DIR"]


if __name__ == "__main__":
    unittest.main()
