"""Unit tests for Codex usage mapper."""

import unittest
from datetime import datetime, timezone

from openusage_linux.core.base import MetricFormat
from openusage_linux.core.providers.codex.mapper import CodexUsageMapper


class TestCodexUsageMapper(unittest.TestCase):
    def test_format_plan_name(self):
        self.assertEqual(CodexUsageMapper.format_plan_name("prolite"), "Pro 5x")
        self.assertEqual(CodexUsageMapper.format_plan_name("pro"), "Pro 20x")
        self.assertEqual(CodexUsageMapper.format_plan_name("team"), "Team")
        self.assertEqual(CodexUsageMapper.format_plan_name("enterprise"), "Enterprise")
        self.assertEqual(CodexUsageMapper.format_plan_name("free"), "Free")
        self.assertEqual(CodexUsageMapper.format_plan_name("custom_tier"), "Custom Tier")
        self.assertIsNone(CodexUsageMapper.format_plan_name(None))
        self.assertIsNone(CodexUsageMapper.format_plan_name(""))

    def test_map_usage_windows_and_spark(self):
        body = {
            "plan_type": "prolite",
            "rate_limit": {
                "allowed": True,
                "primary_window": {
                    "used_percent": 45.0,
                    "limit_window_seconds": 18000,
                    "reset_after_seconds": 3600,
                },
                "secondary_window": {
                    "used_percent": 68.0,
                    "limit_window_seconds": 604800,
                    "reset_after_seconds": 86400,
                },
            },
            "additional_rate_limits": [
                {
                    "limit_name": "GPT-5.3-Codex-Spark",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 12.5,
                            "limit_window_seconds": 604800,
                            "reset_after_seconds": 86400,
                        }
                    },
                }
            ],
            "credits": {
                "has_credits": True,
                "balance": "500",
            },
            "rate_limit_reset_credits": {
                "available_count": 2,
                "credits": [
                    {"status": "available", "expires_at": "2026-08-25T00:00:00Z"},
                    {"status": "consumed", "expires_at": "2026-08-20T00:00:00Z"},
                ],
            },
        }

        now = datetime(2026, 8, 17, 0, 0, 0, tzinfo=timezone.utc)
        plan, lines = CodexUsageMapper.map_usage(body=body, now=now)

        self.assertEqual(plan, "Pro 5x")
        self.assertEqual(len(lines), 5)

        # Check Session line
        session_line = next(l for l in lines if l.label == "Session")
        self.assertEqual(session_line.used, 45.0)
        self.assertEqual(session_line.period_duration_ms, 18000000)

        # Check Weekly line
        weekly_line = next(l for l in lines if l.label == "Weekly")
        self.assertEqual(weekly_line.used, 68.0)
        self.assertEqual(weekly_line.period_duration_ms, 604800000)

        # Check Spark Weekly line
        spark_line = next(l for l in lines if l.label == "Spark Weekly")
        self.assertEqual(spark_line.used, 12.5)

        # Check Rate Limit Resets line
        resets_line = next(l for l in lines if l.label == "Rate Limit Resets")
        self.assertEqual(resets_line.values[0].number, 2.0)
        self.assertEqual(len(resets_line.expiries_at), 1)  # Only the available one

        # Check Extra Usage line
        credits_line = next(l for l in lines if l.label == "Extra Usage")
        # 500 credits * $0.04 = $20.00
        dollar_val = next(v for v in credits_line.values if v.kind == MetricFormat.DOLLARS)
        count_val = next(v for v in credits_line.values if v.kind == MetricFormat.COUNT)
        self.assertEqual(dollar_val.number, 20.0)
        self.assertEqual(count_val.number, 500.0)


if __name__ == "__main__":
    unittest.main()
