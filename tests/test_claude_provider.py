"""Provider-level tests for Claude, including the local-usage path.

Claude Code writes transcripts whichever endpoint served the request, so a user with
no Anthropic login (a gateway, a proxy, an expired session) still has local token
history. These tests pin that path and confirm the logged-in path is unchanged.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from openusage_linux.core.pricing import ModelPricingStore
from openusage_linux.core.pricing_feeds import PricingFeeds
from openusage_linux.core.providers.claude import (
    ERROR_NO_SESSIONS,
    LOCAL_ONLY_NOTE,
    ClaudeProvider,
)
from openusage_linux.core.providers.claude.auth import ClaudeAuthError, ClaudeAuthState, ClaudeOAuth
from openusage_linux.core.providers.claude.scanner import ClaudeLogUsageScanner
from openusage_linux.core.scan_cache import ScanCache

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude"

# The fixture carries fixed dates, so `now` is pinned. Otherwise the provider's
# 30-day window would slide past them and the suite would start failing on its own.
PINNED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class _PinnedDatetime(datetime):
    """A datetime whose now() is fixed, so the scan window never drifts."""

    @classmethod
    def now(cls, tz=None):
        return PINNED_NOW if tz is not None else PINNED_NOW.replace(tzinfo=None)


# Fixture totals, derived by hand from tests/fixtures/claude/projects/demo/session.jsonl:
#   msg-a keeps the complete of two streaming snapshots (2000 in, 1000 5m write,
#   10000 cache read, 500 out), a sidechain replay of it is dropped, msg-b carries a
#   5m/1h split of 300/700, msg-c a legacy cache_creation of 400, the <synthetic>
#   record is skipped, and made-up-model-x is unpriced.
EXPECTED_TOTAL_TOKENS = 15_010
EXPECTED_INPUT = 4_500
EXPECTED_CACHED = 10_000
EXPECTED_OUTPUT = 510
# 2000*3 + 10000*0.30 + 500*15 + 1000*3.75   = 0.020250
# 300*3.75 + 700*6.00                        = 0.005325
# 400*3.75                                   = 0.001500
EXPECTED_COST = 0.027075


def fixture_provider() -> ClaudeProvider:
    """A provider whose scanner reads only the committed fixture and a temp cache."""
    tmp = tempfile.mkdtemp(prefix="openusage-claude-test-")
    return ClaudeProvider(
        scanner=ClaudeLogUsageScanner(
            pricing_store=ModelPricingStore(feeds=PricingFeeds(cache_dir=Path(tmp) / "pricing")),
            cache=ScanCache(cache_file=Path(tmp) / "cache.json"),
        )
    )


class ClaudeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.provider = fixture_provider()
        patch = mock.patch.dict(os.environ, {
            "CLAUDE_CONFIG_DIR": str(FIXTURE_ROOT),
            "XDG_CONFIG_HOME": str(Path(self._tmp.name) / "xdg"),
        }, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        # `CLAUDE_CODE_OAUTH_TOKEN` would make the provider think a login exists.
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_OAUTH_TOKEN", None)
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        clock = mock.patch("openusage_linux.core.providers.claude.datetime", _PinnedDatetime)
        clock.start()
        self.addCleanup(clock.stop)


class TestLocalUsageWithoutLogin(ClaudeTestCase):
    def test_detects_from_transcripts_alone(self):
        self.assertTrue(self.provider.has_local_credentials())
        self.assertTrue(self.provider.has_session_logs())

    def test_snapshot_carries_usage_and_explains_the_missing_limits(self):
        snapshot = self.provider.refresh()
        self.assertFalse(snapshot.is_error)
        self.assertIsNone(snapshot.plan)
        self.assertIsNone(snapshot.account_email)
        self.assertEqual([line.note for line in snapshot.lines], [LOCAL_ONLY_NOTE])
        self.assertEqual([line.kind for line in snapshot.lines], ["no_data"])
        self.assertEqual(snapshot.usage_history.series, snapshot.usage_history.series)

    def test_reports_the_hand_computed_totals(self):
        history = self.provider.refresh().usage_history
        self.assertEqual(sum(entry.total_tokens for entry in history.series), EXPECTED_TOTAL_TOKENS)
        self.assertEqual(sum(entry.input_tokens for entry in history.series), EXPECTED_INPUT)
        self.assertEqual(sum(entry.cached_tokens for entry in history.series), EXPECTED_CACHED)
        self.assertEqual(sum(entry.output_tokens for entry in history.series), EXPECTED_OUTPUT)
        self.assertAlmostEqual(sum(entry.estimated_cost for entry in history.series), EXPECTED_COST, places=6)

    def test_streaming_duplicates_and_sidechain_replays_are_not_double_counted(self):
        history = self.provider.refresh().usage_history
        # A single count of 500 output tokens, not 500 + 0 + 500.
        self.assertEqual(sum(entry.output_tokens for entry in history.series), EXPECTED_OUTPUT)

    def test_synthetic_records_are_ignored(self):
        history = self.provider.refresh().usage_history
        self.assertNotIn("<synthetic>", [summary.model for summary in history.model_usage])

    def test_unpriced_models_keep_their_tokens_and_are_named(self):
        history = self.provider.refresh().usage_history
        unpriced = history.unknown_models_by_day
        self.assertEqual(
            sorted({name for names in unpriced.values() for name in names}),
            ["made-up-model-x"],
        )
        summary = next(m for m in history.model_usage if m.model == "made-up-model-x")
        self.assertEqual(summary.total_tokens, 110)
        self.assertEqual(summary.estimated_cost, 0.0)

    def test_splits_by_local_day(self):
        history = self.provider.refresh().usage_history
        # The fixture's two clusters are exactly 24 hours apart, so they land on two
        # distinct local dates in every timezone, but which dates depends on the
        # runner's offset. Assert the split, not the names.
        self.assertEqual(len(history.series), 2)
        first = datetime.fromisoformat(history.series[0].date)
        second = datetime.fromisoformat(history.series[1].date)
        self.assertEqual((second - first).days, 1)

    def test_no_login_and_no_sessions_is_an_error(self):
        empty = Path(self._tmp.name) / "empty-claude"
        (empty / "projects").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(empty)}):
            provider = fixture_provider()
            self.assertFalse(provider.has_session_logs())
            snapshot = provider.refresh()
        self.assertTrue(snapshot.is_error)
        self.assertEqual(snapshot.error, ERROR_NO_SESSIONS)

    def test_an_expired_login_still_shows_local_usage(self):
        # The README promises tokens for an expired login, and `has_usable_access_token`
        # only checks that the token is non-empty, so an expired session reaches the
        # fallback path rather than the no-candidates one.
        expired = ClaudeAuthState(
            oauth=ClaudeOAuth(
                access_token="expired",
                refresh_token="refresh",
                expires_at_ms=0,
                subscription_type="max",
                rate_limit_tier="20x",
                scopes=["user:profile"],
            ),
            source="file",
            file_path=None,
        )

        def refuse(_state):
            raise ClaudeAuthError(
                "Token expired. Run `claude` to log in again.", allows_fallback=True
            )

        with mock.patch(
            "openusage_linux.core.providers.claude.load_candidates", return_value=[expired]
        ), mock.patch(
            "openusage_linux.core.providers.claude.refresh_access_token", side_effect=refuse
        ):
            snapshot = self.provider.refresh()

        self.assertFalse(snapshot.is_error)
        self.assertEqual(snapshot.note, "Token expired. Run `claude` to log in again.")
        self.assertEqual(
            sum(entry.total_tokens for entry in snapshot.usage_history.series),
            EXPECTED_TOTAL_TOKENS,
        )


class TestLoggedInPathUnchanged(ClaudeTestCase):
    def _state(self):
        return ClaudeAuthState(
            oauth=ClaudeOAuth(
                access_token="token",
                refresh_token=None,
                expires_at_ms=None,
                subscription_type="max",
                rate_limit_tier="20x",
                scopes=["user:profile"],
            ),
            source="file",
            file_path=None,
        )

    def test_live_limits_still_render_and_no_note_is_added(self):
        body = {
            "five_hour": {"utilization": 42.0, "resets_at": "2026-09-14T12:00:00Z"},
            "seven_day": {"utilization": 10.0},
        }
        with mock.patch(
            "openusage_linux.core.providers.claude.load_candidates",
            return_value=[self._state()],
        ), mock.patch(
            "openusage_linux.core.providers.claude.fetch_usage",
            return_value=(body, {}),
        ):
            snapshot = self.provider.refresh()
        self.assertFalse(snapshot.is_error)
        self.assertEqual(snapshot.plan, "Max 20x")
        self.assertEqual([line.label for line in snapshot.lines], ["Session", "Weekly"])
        self.assertNotIn(LOCAL_ONLY_NOTE, [line.note for line in snapshot.lines])
        # Local history still rides along with the live limits.
        self.assertEqual(sum(e.total_tokens for e in snapshot.usage_history.series), EXPECTED_TOTAL_TOKENS)


if __name__ == "__main__":
    unittest.main()
