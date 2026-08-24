"""Fixture tests for GrokLogUsageScanner, ported from upstream Swift tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from openusage_linux.core.pricing import ModelRates
from openusage_linux.core.providers.grok.scanner import GrokLogUsageScanner


DEFAULT_RATES = ModelRates(
    input_per_million=1.0,
    output_per_million=1.0,
    cache_read_per_million=0.1,
    cache_read_is_explicit=True,
)


class StubPricing:
    def __init__(self, known=None):
        self.known = {"grok-build": DEFAULT_RATES, **(known or {})}
        self.supplement = type("Supplement", (), {"canonical_name": staticmethod(lambda model: model)})()
        self.catalog = self

    def find_exact(self, model):
        rates = self.known.get(model)
        return (model, rates) if rates else None

    def find_fuzzy(self, model):
        return None

    def _with_fast_multiplier(self, rates, canonical):
        return rates


GROK_45_RATES = ModelRates(
    input_per_million=2.0,
    output_per_million=6.0,
    cache_read_per_million=0.5,
    cache_write_per_million=2.0,
    cache_read_is_explicit=True,
    cache_write_is_explicit=True,
)
SINCE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def completed_turn(
    timestamp="2026-06-10T10:00:00.000Z",
    model="grok-build",
    input_tokens=0,
    cached=0,
    cache_write=0,
    output=0,
    reasoning=0,
    cost_usd_ticks=None,
    event_id=None,
    agent_timestamp_ms=None,
    numeric_timestamp=False,
    nested=True,
    include_per_model_cost=True,
    additional_models=None,
    session_update="turn_completed",
):
    model_values = {
        "inputTokens": input_tokens,
        "cachedReadTokens": cached,
        "cacheCreationTokens": cache_write,
        "outputTokens": output,
        "reasoningTokens": reasoning,
    }
    if cost_usd_ticks is not None and include_per_model_cost:
        model_values["costUsdTicks"] = cost_usd_ticks
    models = dict(additional_models or {})
    models[model] = model_values
    usage = {"inputTokens": input_tokens, "outputTokens": output, "modelUsage": models}
    if cost_usd_ticks is not None:
        usage["costUsdTicks"] = cost_usd_ticks
    update = {"sessionUpdate": session_update, "usage": usage}
    metadata = {}
    if event_id is not None:
        metadata["eventId"] = event_id
    if agent_timestamp_ms is not None:
        metadata["agentTimestampMs"] = agent_timestamp_ms

    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    object_ = {"timestamp": int(parsed.timestamp()) if numeric_timestamp else timestamp}
    if nested:
        params = {"sessionId": "session-1", "update": update}
        if metadata:
            params["_meta"] = metadata
        object_["params"] = params
        object_["method"] = "session/update"
    else:
        object_["update"] = update
        if metadata:
            object_["_meta"] = metadata
    return json.dumps(object_)


def scan_text(text, pricing=None):
    entries = GrokLogUsageScanner.parse_file(text)
    return GrokLogUsageScanner.aggregate(
        GrokLogUsageScanner.dedup(entries),
        since=SINCE,
        pricing_store=pricing or StubPricing(),
    )


class TestGrokLogUsageScanner(unittest.TestCase):
    def test_uses_recorded_per_model_cost_and_does_not_count_reasoning_twice(self):
        history = scan_text(
            completed_turn(
                model="grok-4.6-build",
                input_tokens=1_000_000,
                cached=700_000,
                output=50_000,
                reasoning=20_000,
                cost_usd_ticks=2_357_158_800,
                event_id="turn-1",
            )
        )
        day = history.series[0]
        self.assertEqual(day.total_tokens, 1_050_000)
        self.assertAlmostEqual(day.estimated_cost, 0.23571588, places=8)
        self.assertEqual([model.model for model in day.models], ["grok-4.6-build"])

    def test_falls_back_to_shared_pricing_and_separates_cache_buckets(self):
        history = scan_text(
            completed_turn(
                model="grok-4.5-build",
                input_tokens=1_000_000,
                cached=700_000,
                cache_write=100_000,
                output=250_000,
            ),
            pricing=StubPricing({"grok-4.5-build": GROK_45_RATES}),
        )
        day = history.series[0]
        self.assertEqual(day.total_tokens, 1_250_000)
        self.assertAlmostEqual(day.estimated_cost, 2.45, places=4)

    def test_splits_completed_turn_across_models_without_duplicating_the_event(self):
        history = scan_text(
            completed_turn(
                model="grok-4.5-build",
                input_tokens=100,
                output=20,
                cost_usd_ticks=1_000_000_000,
                event_id="shared-turn",
                additional_models={
                    "grok-4.6-build": {
                        "inputTokens": 200,
                        "outputTokens": 30,
                        "costUsdTicks": 2_000_000_000,
                    }
                },
            )
        )
        day = history.series[0]
        self.assertEqual(day.total_tokens, 350)
        self.assertAlmostEqual(day.estimated_cost, 0.3, places=4)
        self.assertEqual({model.model for model in day.models}, {"grok-4.5-build", "grok-4.6-build"})

    def test_single_model_falls_back_to_turn_level_recorded_cost(self):
        history = scan_text(
            completed_turn(
                model="grok-4.6-build",
                input_tokens=1_000_000,
                cost_usd_ticks=1_250_000_000,
                include_per_model_cost=False,
            )
        )
        self.assertAlmostEqual(history.series[0].estimated_cost, 0.125, places=4)

    def test_recorded_cost_allows_unknown_model_without_pricing_warning(self):
        history = scan_text(
            completed_turn(
                model="grok-future-model",
                input_tokens=500_000,
                cost_usd_ticks=3_000_000_000,
            )
        )
        self.assertAlmostEqual(history.series[0].estimated_cost, 0.3, places=4)
        self.assertEqual(history.unknown_models_by_day, {})

    def test_unknown_model_without_recorded_cost_is_excluded(self):
        history = scan_text(
            completed_turn(model="grok-future-model", input_tokens=500_000)
        )
        self.assertEqual(history.series, [])
        self.assertEqual(history.unknown_models_by_day["2026-06-10"], ["grok-future-model"])

    def test_zero_recorded_cost_still_counts_measured_tokens(self):
        history = scan_text(
            completed_turn(model="grok-future-model", input_tokens=500, cost_usd_ticks=0)
        )
        day = history.series[0]
        self.assertEqual(day.total_tokens, 500)
        self.assertEqual(day.estimated_cost, 0)

    def test_prefers_millisecond_agent_timestamp_over_coarse_outer_timestamp(self):
        line = completed_turn(
            timestamp="2026-06-09T10:00:00.000Z",
            input_tokens=1_000,
            agent_timestamp_ms=1_781_089_200_456,
        )
        entry = GrokLogUsageScanner.parse_file(line)[0]
        self.assertAlmostEqual(entry.timestamp.timestamp(), 1_781_089_200.456, places=3)

    def test_parses_unix_second_timestamps(self):
        history = scan_text(completed_turn(input_tokens=1_000, numeric_timestamp=True))
        self.assertEqual(history.series[0].date, "2026-06-10")

    def test_parses_top_level_update_envelope(self):
        history = scan_text(completed_turn(input_tokens=1_000, nested=False))
        self.assertEqual(history.series[0].total_tokens, 1_000)

    def test_skips_incomplete_malformed_and_out_of_window_turns(self):
        history = scan_text(
            "\n".join(
                [
                    completed_turn(input_tokens=1_000, session_update="turn_started"),
                    "{broken turn_completed",
                    completed_turn(timestamp="2026-05-30T10:00:00.000Z", input_tokens=2_000),
                    completed_turn(input_tokens=3_000),
                ]
            )
        )
        self.assertEqual(history.series[0].total_tokens, 3_000)

    def test_copied_event_ids_are_counted_once(self):
        line = completed_turn(input_tokens=1_000, event_id="duplicate-event")
        history = scan_text("\n".join([line, line]))
        self.assertEqual(history.series[0].total_tokens, 1_000)


def _write_home(files):
    home = tempfile.mkdtemp(prefix="openusage-grok-")
    sessions = Path(home) / "sessions"
    sessions.mkdir()
    for relative, contents in files.items():
        path = sessions / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return home


class TestGrokSessionDiscovery(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("GROK_HOME")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GROK_HOME", None)
        else:
            os.environ["GROK_HOME"] = self._old

    def _scan(self, home, now="2026-06-18T12:00:00.000Z"):
        os.environ["GROK_HOME"] = f" {home} "
        return GrokLogUsageScanner(pricing_store=StubPricing()).scan(
            days_back=30,
            now=datetime.fromisoformat(now.replace("Z", "+00:00")),
        )

    def test_recursively_scans_session_ledgers_and_ignores_other_jsonl_files(self):
        first = completed_turn(input_tokens=100, event_id="copied-turn")
        second = completed_turn(timestamp="2026-06-11T10:00:00.000Z", input_tokens=200)
        ignored = completed_turn(timestamp="2026-06-12T10:00:00.000Z", input_tokens=300)
        home = _write_home(
            {
                "project-a/session-a/updates.jsonl": first,
                "project-b/session-b/updates.jsonl": second,
                "project-b/session-b/debug.jsonl": ignored,
                "project-c/copied-session/updates.jsonl": first,
            }
        )
        history = self._scan(home)
        self.assertEqual([entry.date for entry in history.series], ["2026-06-10", "2026-06-11"])
        self.assertEqual([entry.total_tokens for entry in history.series], [100, 200])

    def test_skips_subagent_sessions_already_included_in_coordinator_totals(self):
        home = _write_home(
            {
                "project/coordinator/updates.jsonl": completed_turn(
                    model="grok-4.6-build", input_tokens=300, cost_usd_ticks=30_000_000_000, event_id="coordinator-turn"
                ),
                "project/coordinator/summary.json": '{"session_kind":"coordinator"}',
                "project/coordinator/subagents/worker/updates.jsonl": completed_turn(
                    timestamp="2026-06-10T10:01:00.000Z",
                    model="grok-4.6-build",
                    input_tokens=100,
                    cost_usd_ticks=10_000_000_000,
                    event_id="subagent-turn",
                ),
                "project/coordinator/subagents/worker/summary.json": '{"session_kind":"subagent"}',
                "project/fork/updates.jsonl": completed_turn(
                    timestamp="2026-06-10T10:02:00.000Z",
                    model="grok-4.6-build",
                    input_tokens=200,
                    cost_usd_ticks=20_000_000_000,
                    event_id="fork-turn",
                ),
                "project/fork/summary.json": '{"session_kind":"subagent_fork"}',
                "project/legacy/updates.jsonl": completed_turn(
                    timestamp="2026-06-10T11:00:00.000Z",
                    model="grok-4.6-build",
                    input_tokens=50,
                    cost_usd_ticks=5_000_000_000,
                    event_id="legacy-turn",
                ),
            }
        )
        day = self._scan(home).series[0]
        self.assertEqual(day.total_tokens, 350)
        self.assertAlmostEqual(day.estimated_cost, 3.5, places=4)

    def test_skips_session_with_malformed_summary_instead_of_guessing_its_kind(self):
        home = _write_home(
            {
                "project/session/updates.jsonl": completed_turn(input_tokens=1_000),
                "project/session/summary.json": "{invalid",
            }
        )
        self.assertIsNone(self._scan(home))

    def test_returns_none_when_session_ledgers_are_missing(self):
        os.environ["GROK_HOME"] = tempfile.mkdtemp(prefix="openusage-no-grok-")
        self.assertIsNone(GrokLogUsageScanner(pricing_store=StubPricing()).scan())

    def test_does_not_fall_back_to_the_debug_log_when_sessions_are_missing(self):
        home = tempfile.mkdtemp(prefix="openusage-grok-empty-")
        logs = Path(home) / "logs"
        logs.mkdir()
        (logs / "unified.jsonl").write_text("debug log content", encoding="utf-8")
        os.environ["GROK_HOME"] = home
        self.assertIsNone(GrokLogUsageScanner(pricing_store=StubPricing()).scan())


if __name__ == "__main__":
    unittest.main()
