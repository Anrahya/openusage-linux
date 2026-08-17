"""Fixture tests for the Cursor provider (payload shapes ported from upstream)."""

import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest

from openusage_linux.core.base import MetricFormat
from openusage_linux.core.providers.cursor.auth import (
    load_auth_state,
    needs_refresh,
    token_subject,
    user_id_from_token,
)
from openusage_linux.core.providers.cursor.csv import build_history, parse_usage_csv
from openusage_linux.core.providers.cursor.mapper import map_request_based, map_usage

INDIVIDUAL_USAGE = {
    "usage": {
        "enabled": True,
        "planUsage": {
            "limit": 20000,  # cents
            "totalPercentUsed": 37.5,
            "totalSpend": 7500,
            "autoPercentUsed": 12.0,
            "apiPercentUsed": 4.0,
        },
        "spendLimitUsage": {"limitType": "individual", "pooledLimit": 0},
        "billingCycleStart": 1818000000000,
        "billingCycleEnd": 1820678400000,
    }
}

TEAM_USAGE = {
    "usage": {
        "enabled": True,
        "planUsage": {"limit": 100000, "totalSpend": 40000},
        "spendLimitUsage": {"limitType": "team", "pooledLimit": 100000},
        "billingCycleStart": 1818000000000,
        "billingCycleEnd": 1820678400000,
    }
}


def make_jwt(sub, exp=None):
    payload = {"sub": sub}
    if exp is not None:
        payload["exp"] = exp
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.sig"


class TestCursorMapper(unittest.TestCase):
    def test_individual_percent_meters(self):
        plan, lines = map_usage(INDIVIDUAL_USAGE, "pro")
        labels = [line.label for line in lines]
        self.assertEqual(labels, ["Total usage", "Auto usage", "API usage"])
        self.assertEqual(lines[0].used, 37.5)
        self.assertEqual(lines[0].format, MetricFormat.PERCENT)
        self.assertIsNotNone(lines[0].resets_at)
        self.assertEqual(plan, "pro")

    def test_team_dollar_meter_and_credits(self):
        credits = {"hasCreditGrants": True, "totalCents": 50000, "usedCents": 10000}
        plan, lines = map_usage(TEAM_USAGE, "team", credits, stripe_balance_cents=500)
        credits_line = next(line for line in lines if line.label == "Credits")
        self.assertAlmostEqual(credits_line.values[0].number, 405.0)  # 50000-10000+500 cents
        total = next(line for line in lines if line.label == "Total usage")
        self.assertEqual(total.format, MetricFormat.DOLLARS)
        self.assertAlmostEqual(total.used, 400.0)
        self.assertAlmostEqual(total.limit, 1000.0)

    def test_no_subscription(self):
        from openusage_linux.core.providers.cursor.mapper import CursorMapperError
        with self.assertRaises(CursorMapperError):
            map_usage({"usage": {"enabled": False}})

    def test_request_based_fallback(self):
        body = {"usage": {"gpt-4": {"maxRequestUsage": 500, "numRequests": 123}}}
        _, lines = map_request_based(body)
        self.assertEqual(lines[0].label, "Requests")
        self.assertEqual(lines[0].used, 123)
        self.assertEqual(lines[0].limit, 500)


class TestCursorAuth(unittest.TestCase):
    def test_jwt_helpers(self):
        token = make_jwt("workos|user-123", exp=time.time() + 3600)
        self.assertEqual(token_subject(token), "workos|user-123")
        self.assertEqual(user_id_from_token(token), "user-123")
        self.assertFalse(needs_refresh(token))
        self.assertTrue(needs_refresh(make_jwt("x", exp=time.time() - 10)))
        self.assertTrue(needs_refresh(None))

    def test_reads_state_vscdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor_dir = os.path.join(tmp, "Cursor", "User", "globalStorage")
            os.makedirs(cursor_dir)
            db_path = os.path.join(cursor_dir, "state.vscdb")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value TEXT)")
            conn.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", make_jwt("a|u1")))
            conn.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/refreshToken", "refresh"))
            conn.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/stripeMembershipType", "PRO"))
            conn.commit()
            conn.close()

            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                state = load_auth_state()
                self.assertIsNotNone(state)
                self.assertEqual(state.refresh_token, "refresh")
                self.assertEqual(state.membership_type, "pro")
                self.assertEqual(user_id_from_token(state.access_token), "u1")
            finally:
                if old is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = old


CSV_EXPORT = """Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens
2026-08-17T10:00:00Z,gpt-5.6-sol,"1,000",2000,500,100
2026-08-17T11:00:00Z,gpt-5.6-sol,0,100,0,50
2026-08-17,broken-model,not-a-number,0,0,0
"""


class TestCursorCSV(unittest.TestCase):
    def test_parses_rows_and_builds_history(self):
        rows = parse_usage_csv(CSV_EXPORT)
        self.assertEqual(len(rows), 2)  # malformed row skipped
        self.assertEqual(rows[0].cache_write_tokens, 1000)
        history = build_history(rows)
        self.assertEqual(len(history.series), 1)
        day = history.series[0]
        self.assertEqual(day.total_tokens, 1000 + 2000 + 500 + 100 + 100 + 50)
        self.assertEqual(history.model_usage[0].model, "gpt-5.6-sol")

    def test_rejects_structurally_broken_csv(self):
        self.assertIsNone(parse_usage_csv(""))
        self.assertIsNone(parse_usage_csv("Date,Model\nx,y"))  # missing required columns


if __name__ == "__main__":
    unittest.main()
