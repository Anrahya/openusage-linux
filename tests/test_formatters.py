"""Unit tests for snapshot serialization semantics."""

import unittest
from datetime import date, timedelta

from openusage_linux.cli.formatters import snapshot_to_dict
from openusage_linux.core.base import (
    DailyUsageSeries,
    MetricLine,
    Provider,
    ProviderSnapshot,
    ProviderUsageHistory,
)


class TestFormatters(unittest.TestCase):
    def test_yesterday_is_not_reported_as_today(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        history = ProviderUsageHistory(
            series=[DailyUsageSeries(date=yesterday, total_tokens=123, estimated_cost=4.56)]
        )
        snapshot = ProviderSnapshot(
            provider=Provider(id="codex", display_name="Codex", icon_name="codex"),
            usage_history=history,
        )

        spend = snapshot_to_dict(snapshot)["spend_history"]

        self.assertEqual(spend["today_tokens"], 0)
        self.assertEqual(spend["today_cost"], 0.0)
        self.assertEqual(spend["total_tokens_30d"], 123)
        self.assertEqual(spend["total_cost_30d"], 4.56)

    def test_rate_limits_carry_period_and_mac_severity_bands(self):
        snapshot = ProviderSnapshot(
            provider=Provider(id="codex", display_name="Codex", icon_name="codex"),
            lines=[
                MetricLine.progress("Session", 85.0, period_duration_ms=18_000_000),
                MetricLine.progress("Weekly", 40.0, period_duration_ms=604_800_000),
            ],
        )

        rate_limits = snapshot_to_dict(snapshot)["rate_limits"]

        self.assertEqual(rate_limits[0]["period_seconds"], 18000)
        self.assertEqual(rate_limits[0]["class"], "warning")
        self.assertEqual(rate_limits[1]["period_seconds"], 604800)
        self.assertEqual(rate_limits[1]["class"], "normal")


if __name__ == "__main__":
    unittest.main()
