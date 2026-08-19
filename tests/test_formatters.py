"""Unit tests for snapshot serialization semantics."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone

from openusage_linux.cli.formatters import (
    format_countdown,
    format_progress_bar,
    format_token_count,
    render_waybar_json,
    snapshot_to_dict,
)
from openusage_linux.core.base import (
    DailyUsageSeries,
    MetricFormat,
    MetricLine,
    MetricValue,
    ModelUsageSummary,
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
        self.assertEqual(spend["daily_series"][0]["date"], yesterday)
        self.assertEqual(spend["daily_series"][0]["tokens"], 123)

    def test_daily_series_includes_per_day_models(self):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        history = ProviderUsageHistory(
            series=[
                DailyUsageSeries(
                    date=yesterday,
                    total_tokens=100,
                    estimated_cost=1.0,
                    models=[ModelUsageSummary(model="gpt-yesterday", total_tokens=100, estimated_cost=1.0)],
                ),
                DailyUsageSeries(
                    date=today,
                    total_tokens=50,
                    estimated_cost=0.5,
                    models=[ModelUsageSummary(model="gpt-today", total_tokens=50, estimated_cost=0.5)],
                ),
            ],
            model_usage=[
                ModelUsageSummary(model="gpt-yesterday", total_tokens=100, estimated_cost=1.0),
                ModelUsageSummary(model="gpt-today", total_tokens=50, estimated_cost=0.5),
            ],
        )
        spend = snapshot_to_dict(
            ProviderSnapshot(
                provider=Provider(id="codex", display_name="Codex", icon_name="codex"),
                usage_history=history,
            )
        )["spend_history"]

        by_date = {entry["date"]: entry for entry in spend["daily_series"]}
        self.assertEqual(by_date[today]["tokens"], 50)
        self.assertEqual(by_date[today]["models"][0]["model"], "gpt-today")
        self.assertEqual(by_date[yesterday]["tokens"], 100)
        self.assertEqual(by_date[yesterday]["models"][0]["model"], "gpt-yesterday")
        self.assertEqual(spend["today_tokens"], 50)
        self.assertEqual(spend["total_tokens_30d"], 150)

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

    def test_format_countdown_edges(self):
        now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(format_countdown(None, now=now), "")
        self.assertEqual(format_countdown(now, now=now), "resets now")
        self.assertEqual(
            format_countdown(now + timedelta(seconds=45), now=now),
            "resets now",
        )
        self.assertEqual(
            format_countdown(now + timedelta(minutes=12), now=now),
            "resets in 12m",
        )
        self.assertEqual(
            format_countdown(now + timedelta(hours=3, minutes=10), now=now),
            "resets in 3h 10m",
        )
        self.assertEqual(
            format_countdown(now + timedelta(days=2, hours=4), now=now),
            "resets in 2d 4h",
        )

    def test_format_progress_bar_does_not_fill_at_99(self):
        bar = format_progress_bar(99.0, width=16)
        self.assertIn("░", bar)
        self.assertNotIn("\033[31m", format_progress_bar(79.9, width=8))
        self.assertIn("\033[33m", format_progress_bar(80.0, width=8))
        self.assertIn("\033[31m", format_progress_bar(90.0, width=8))

    def test_format_token_count_boundaries(self):
        self.assertEqual(format_token_count(999), "999")
        self.assertEqual(format_token_count(1000), "1.0k")
        self.assertEqual(format_token_count(1_000_000), "1.00M")

    def test_snapshot_to_dict_error_and_credits(self):
        provider = Provider(id="codex", display_name="Codex", icon_name="codex")
        error = snapshot_to_dict(ProviderSnapshot.error_snapshot(provider, "boom"))
        self.assertTrue(error["is_error"])
        self.assertEqual(error["class"], "critical")
        self.assertEqual(error["text"], "Codex: Err")

        snapshot = ProviderSnapshot(
            provider=provider,
            lines=[
                MetricLine.values_line(
                    "Rate Limit Resets",
                    [MetricValue(number=2, kind=MetricFormat.COUNT, label="available")],
                ),
                MetricLine.values_line(
                    "Extra Usage",
                    [
                        MetricValue(number=1.20, kind=MetricFormat.DOLLARS),
                        MetricValue(number=30, kind=MetricFormat.COUNT, label="credits"),
                    ],
                ),
            ],
        )
        credits = snapshot_to_dict(snapshot)["credits"]
        self.assertEqual(credits["rate_limit_resets"], 2)
        self.assertEqual(credits["extra_usage_dollars"], 1.20)
        self.assertEqual(credits["extra_usage_credits"], 30)

    def test_render_waybar_json_picks_most_constrained_provider(self):
        codex = ProviderSnapshot(
            provider=Provider(id="codex", display_name="Codex", icon_name="codex"),
            lines=[MetricLine.progress("Weekly", 20.0)],
        )
        claude = ProviderSnapshot(
            provider=Provider(id="claude", display_name="Claude", icon_name="claude"),
            lines=[MetricLine.progress("Weekly", 85.0)],
        )
        payload = json.loads(render_waybar_json([codex, claude], prefs={
            "period": "yesterday",
            "metric": "Tokens",
            "refresh_interval": 30,
            "show_total_spend": False,
        }))
        self.assertEqual(payload["provider"]["id"], "claude")
        self.assertEqual(payload["class"], "warning")
        self.assertEqual(payload["percentage"], 85)
        self.assertEqual(len(payload["providers"]), 2)
        self.assertEqual(payload["prefs"]["period"], "yesterday")
        self.assertEqual(payload["prefs"]["metric"], "Tokens")
        self.assertEqual(payload["prefs"]["refresh_interval"], 30)
        self.assertFalse(payload["prefs"]["show_total_spend"])


if __name__ == "__main__":
    unittest.main()
