"""Offline tests for the runtime pricing feed refresh.

Every test injects its own fetch, so nothing here touches the network.
"""

import json
import tempfile
import unittest
from pathlib import Path

from openusage_linux.core.pricing import ModelPricingStore, rates_from_compact
from openusage_linux.core.pricing_feeds import (
    FAILURE_RETRY_SECONDS,
    LITELLM_URL,
    MODELS_DEV_URL,
    REFRESH_INTERVAL_SECONDS,
    SUPPLEMENT_URL,
    TIMEOUT_SECONDS,
    TOTAL_BUDGET_SECONDS,
    NotModified,
    PricingFeeds,
    compact_from_litellm,
    compact_from_models_dev,
    has_supplement_content,
    valid_supplement,
)

LITELLM_FIXTURE = {
    "gpt-example": {
        "input_cost_per_token": 0.0000025,
        "output_cost_per_token": 0.000010,
        "cache_read_input_token_cost": 0.00000025,
    },
    "gpt-example-no-cache-cost": {
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000004,
    },
    "gpt-example-free-cache-read": {
        "input_cost_per_token": 0.000030,
        "output_cost_per_token": 0.000180,
        "cache_read_input_token_cost": 0.0,
    },
    "gpt-example-stub": {"input_cost_per_token": 0.000001},
    "claude-example-fast": {
        "input_cost_per_token": 0.000005,
        "output_cost_per_token": 0.000025,
        "provider_specific_entry": {"fast": 2.0},
    },
    "gemini-example-long": {
        "input_cost_per_token": 0.000002,
        "output_cost_per_token": 0.000010,
        "input_cost_per_token_above_200k_tokens": 0.000004,
        "output_cost_per_token_above_200k_tokens": 0.000020,
    },
}

MODELS_DEV_FIXTURE = {
    "zzz-later-provider": {
        "models": {"shared-model": {"cost": {"input": 9.0, "output": 90.0}}}
    },
    "aaa-first-provider": {
        "models": {
            "shared-model": {"cost": {"input": 1.0, "output": 10.0}},
            "solo-model": {"cost": {"input": 2.0, "output": 20.0, "cache_read": 0.2}},
        }
    },
}

SUPPLEMENT_FIXTURE = {
    "updated_at": "2026-09-11T18:04:26Z",
    "pricing": {"override-model": {"input_per_million": 5.0, "output_per_million": 30.0}},
    "alias_rules": [{"pattern": "^alias-me$", "canonical": "override-model"}],
    "fast_multipliers": {"override-model": 2.0},
}


class FakeFetch:
    """Records calls and returns queued responses."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, etag):
        self.calls.append((url, etag))
        result = self.responses.get(url, KeyError(url))
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(etag)
        return result


def body(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def all_sources() -> dict:
    """Every source answering successfully, so the gate is what decides."""
    return {
        LITELLM_URL: (body(LITELLM_FIXTURE), '"etag-1"'),
        MODELS_DEV_URL: (body(MODELS_DEV_FIXTURE), '"etag-3"'),
        SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"etag-2"'),
    }


class TestTransforms(unittest.TestCase):
    def test_litellm_costs_scale_to_per_million(self):
        models = compact_from_litellm(LITELLM_FIXTURE)
        entry = models["gpt-example"]
        self.assertAlmostEqual(entry["i"], 2.5)
        self.assertAlmostEqual(entry["o"], 10.0)
        self.assertAlmostEqual(entry["cr"], 0.25)

    def test_litellm_defaults_cache_write_to_input_and_cache_read_to_a_tenth(self):
        entry = compact_from_litellm(LITELLM_FIXTURE)["gpt-example-no-cache-cost"]
        self.assertAlmostEqual(entry["cw"], 1.0)
        self.assertAlmostEqual(entry["cr"], 0.1)
        self.assertIs(entry["cre"], False)

    def test_litellm_keeps_a_published_zero_cache_read_as_explicit(self):
        entry = compact_from_litellm(LITELLM_FIXTURE)["gpt-example-free-cache-read"]
        self.assertEqual(entry["cr"], 0.0)
        # No `cre` flag means the rate was published, not synthesized, so the loader
        # must not bump it to a tenth of input.
        self.assertNotIn("cre", entry)
        rates = rates_from_compact(entry)
        self.assertEqual(rates.cache_read_per_million, 0.0)
        self.assertTrue(rates.cache_read_is_explicit)

    def test_litellm_skips_entries_missing_either_cost(self):
        models = compact_from_litellm(LITELLM_FIXTURE)
        self.assertNotIn("gpt-example-stub", models)

    def test_litellm_keeps_shape_without_a_cost_dictionary(self):
        self.assertEqual(compact_from_litellm({"broken": "not-a-dict"}), {})
        self.assertEqual(compact_from_litellm(None), {})

    def test_litellm_carries_fast_multiplier_and_long_context_rates(self):
        models = compact_from_litellm(LITELLM_FIXTURE)
        self.assertAlmostEqual(models["claude-example-fast"]["fast"], 2.0)
        long_entry = models["gemini-example-long"]
        self.assertAlmostEqual(long_entry["ia"], 4.0)
        self.assertAlmostEqual(long_entry["oa"], 20.0)

    def test_models_dev_lets_the_first_sorted_provider_win(self):
        models = compact_from_models_dev(MODELS_DEV_FIXTURE)
        # "aaa-first-provider" sorts before "zzz-later-provider".
        self.assertAlmostEqual(models["shared-model"]["i"], 1.0)
        self.assertAlmostEqual(models["shared-model"]["o"], 10.0)

    def test_models_dev_reads_costs_already_per_million(self):
        models = compact_from_models_dev(MODELS_DEV_FIXTURE)
        entry = models["solo-model"]
        self.assertAlmostEqual(entry["i"], 2.0)
        self.assertAlmostEqual(entry["cr"], 0.2)
        self.assertNotIn("cre", entry)

    def test_models_dev_ignores_shapes_it_cannot_read(self):
        self.assertEqual(compact_from_models_dev({"p": {"models": {"m": {"cost": {}}}}}), {})
        self.assertEqual(compact_from_models_dev({"p": "not-a-dict"}), {})


class TestRefreshGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_dir = Path(self.tmp.name)
        self.now = 1_000_000.0

    def make_feeds(self, responses):
        fetch = FakeFetch(responses)
        return PricingFeeds(cache_dir=self.cache_dir, fetch=fetch, now=lambda: self.now), fetch

    def test_fetches_when_no_cache_exists(self):
        feeds, fetch = self.make_feeds({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"etag-1"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"etag-2"'),
        })
        changed = feeds.refresh()
        self.assertEqual(sorted(changed), ["litellm", "models.dev", "supplement"])
        self.assertEqual(len(fetch.calls), 3)

    def test_skips_sources_fetched_within_the_interval(self):
        feeds, fetch = self.make_feeds(all_sources())
        feeds.refresh()
        self.now += REFRESH_INTERVAL_SECONDS - 1
        self.assertEqual(feeds.due_sources(), [])
        fetch.calls.clear()
        self.assertEqual(feeds.refresh(), {})
        self.assertEqual(fetch.calls, [])

    def test_refetches_once_the_interval_elapses(self):
        feeds, _ = self.make_feeds(all_sources())
        feeds.refresh()
        self.now += REFRESH_INTERVAL_SECONDS + 1
        self.assertEqual(sorted(feeds.due_sources()), ["litellm", "models.dev", "supplement"])

    def test_failure_backs_off_for_the_retry_window(self):
        feeds, fetch = self.make_feeds({
            LITELLM_URL: OSError("network down"),
            SUPPLEMENT_URL: OSError("network down"),
        })
        feeds.refresh()
        self.assertEqual(sorted(feeds.due_sources()), [])
        self.now += FAILURE_RETRY_SECONDS + 1
        self.assertEqual(sorted(feeds.due_sources()), ["litellm", "models.dev", "supplement"])

    def test_not_modified_keeps_the_cached_copy_and_clears_the_failure(self):
        feeds, _ = self.make_feeds(all_sources())
        feeds.refresh()
        cached_before = feeds.cache_file("litellm").read_bytes()

        self.now += REFRESH_INTERVAL_SECONDS + 1
        feeds._fetch = FakeFetch({
            LITELLM_URL: NotModified(),
            SUPPLEMENT_URL: NotModified(),
        })
        changed = feeds.refresh()
        self.assertEqual(changed["litellm"], False)
        self.assertEqual(feeds.cache_file("litellm").read_bytes(), cached_before)
        self.assertIsNone(feeds.state_for("litellm")["failed_at"])

    def test_sends_the_stored_etag(self):
        feeds, fetch = self.make_feeds(all_sources())
        feeds.refresh()
        self.now += REFRESH_INTERVAL_SECONDS + 1
        feed2, fetch2 = self.make_feeds({})
        feed2._fetch = fetch2
        feed2.refresh()
        sent = dict(fetch2.calls)
        self.assertEqual(sent[LITELLM_URL], '"etag-1"')

    def test_accepts_the_etag_via_a_callback(self):
        feeds, _ = self.make_feeds({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"fresh"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), None),
        })
        feeds.refresh()
        self.assertEqual(feeds.state_for("litellm")["etag"], '"fresh"')

    def test_garbage_body_never_replaces_a_good_cache(self):
        feeds, _ = self.make_feeds(all_sources())
        feeds.refresh()
        good = feeds.cache_file("litellm").read_bytes()

        self.now += REFRESH_INTERVAL_SECONDS + 1
        feeds._fetch = FakeFetch({
            LITELLM_URL: (b"<html>not json</html>", '"etag-1"'),
            SUPPLEMENT_URL: NotModified(),
        })
        changed = feeds.refresh()
        self.assertEqual(changed["litellm"], False)
        self.assertEqual(feeds.cache_file("litellm").read_bytes(), good)
        self.assertIsNotNone(feeds.state_for("litellm")["failed_at"])

    def test_a_body_that_transforms_to_nothing_is_a_failure(self):
        feeds, _ = self.make_feeds({
            LITELLM_URL: (body({"only-a-stub": {"input_cost_per_token": 0.001}}), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        changed = feeds.refresh()
        self.assertEqual(changed["litellm"], False)
        self.assertFalse(feeds.cache_file("litellm").exists())

    def test_supplement_cache_is_stored_verbatim(self):
        feeds, _ = self.make_feeds({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        feeds.refresh()
        stored = json.loads(feeds.cache_file("supplement").read_text())
        self.assertEqual(stored, SUPPLEMENT_FIXTURE)
        self.assertEqual(feeds.load_cached()["supplement"]["pricing"]["override-model"]["input_per_million"], 5.0)

    def test_load_cached_skips_missing_and_unreadable_files(self):
        feeds, _ = self.make_feeds({})
        self.assertEqual(feeds.load_cached(), {})
        feeds.cache_file("litellm").write_text("{not json")
        self.assertEqual(feeds.load_cached(), {})

    def test_an_idle_check_does_not_rewrite_the_state_file(self):
        # The GNOME extension runs the CLI every minute, so an idle gate check must
        # stay read-only.
        feeds, _ = self.make_feeds(all_sources())
        feeds.refresh()
        mtime = feeds.state_file.stat().st_mtime_ns
        self.now += 1
        self.assertEqual(feeds.refresh(), {})
        self.assertEqual(feeds.state_file.stat().st_mtime_ns, mtime)

    def test_force_refetches_within_the_interval(self):
        feeds, fetch = self.make_feeds({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"e"'),
            MODELS_DEV_URL: (body(MODELS_DEV_FIXTURE), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        feeds.refresh()
        fetch.calls.clear()
        feeds.refresh(force=True)
        self.assertEqual(len(fetch.calls), 3)


class TestStoreIntegration(unittest.TestCase):
    """The layer order must hold however many times a refresh runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_dir = Path(self.tmp.name)

    def _store(self, responses, now=None):
        fetch = FakeFetch(responses)
        feeds = PricingFeeds(cache_dir=self.cache_dir, fetch=fetch, now=now or (lambda: 1_000_000.0))
        return ModelPricingStore(feeds=feeds), fetch

    def test_fetched_catalog_prices_models_the_snapshot_lacks(self):
        store, _ = self._store({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        self.assertIsNone(store.lookup("gpt-example"))
        store.sync_pricing_feeds()
        rates = store.lookup("gpt-example")
        self.assertIsNotNone(rates)
        self.assertAlmostEqual(rates.input_per_million, 2.5)

    def test_supplement_override_outranks_a_fetched_catalog(self):
        # The fetched catalog knows `override-model` at a different price; the
        # supplement is the deliberate correction and must still win.
        litellm = {
            "override-model": {
                "input_cost_per_token": 0.000001,
                "output_cost_per_token": 0.000002,
            }
        }
        store, _ = self._store({
            LITELLM_URL: (body(litellm), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        store.sync_pricing_feeds()
        rates = store.lookup("override-model")
        self.assertAlmostEqual(rates.input_per_million, 5.0)
        self.assertAlmostEqual(rates.output_per_million, 30.0)

    def test_refreshing_twice_does_not_duplicate_alias_rules(self):
        store, _ = self._store({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        store.sync_pricing_feeds()
        first = len(store.supplement.alias_rules)
        store.sync_pricing_feeds(force=True)
        self.assertEqual(len(store.supplement.alias_rules), first)
        self.assertEqual(store.canonical_name("alias-me"), "override-model")

    def test_a_refresh_clears_memoized_resolutions(self):
        store, _ = self._store({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        # Resolve before the feed exists, then again after: the first answer must not
        # be served from the memo once the catalog has changed.
        self.assertIsNone(store.lookup("gpt-example"))
        store.sync_pricing_feeds()
        self.assertIsNotNone(store.lookup("gpt-example"))

    def test_fetched_fast_multiplier_applies(self):
        store, _ = self._store({
            LITELLM_URL: (body(LITELLM_FIXTURE), '"e"'),
            SUPPLEMENT_URL: (body(SUPPLEMENT_FIXTURE), '"e"'),
        })
        store.sync_pricing_feeds()
        rates = store.lookup("claude-example-fast")
        self.assertAlmostEqual(rates.fast_multiplier, 2.0)
        slow = rates.cost_dollars(input_tokens=1_000_000, cached_tokens=0, output_tokens=0)
        fast = rates.cost_dollars(input_tokens=1_000_000, cached_tokens=0, output_tokens=0, is_fast=True)
        self.assertAlmostEqual(fast, slow * 2.0)

    def test_an_unreachable_feed_leaves_the_store_usable(self):
        store, _ = self._store({
            LITELLM_URL: OSError("offline"),
            SUPPLEMENT_URL: OSError("offline"),
        })
        store.sync_pricing_feeds()
        self.assertIsNotNone(store.lookup("gpt-5.3-codex"))


class TestFeedValidation(unittest.TestCase):
    """A bad feed must never be cached, and a bad cache must never break startup."""

    def test_bodies_that_would_break_the_loader_are_rejected(self):
        for payload in (
            {"pricing": {"m": {"input_per_million": None}}},
            {"pricing": {"m": {"input_per_million": "abc"}}},
            {"pricing": {"m": "not-an-entry"}},
            {"pricing": [1, 2]},
            {"fast_multipliers": {"gpt-5": True}},
            {"fast_multipliers": {"gpt-5": None}},
            {"alias_rules": [{"pattern": 1, "canonical": "x"}]},
            {"updated_at": 5},
        ):
            self.assertFalse(valid_supplement(payload), payload)

    def test_readable_bodies_are_accepted(self):
        for payload in (
            {"pricing": {"m": {"input_per_million": 3.0, "output_per_million": 15.0}}},
            {"pricing": {"m": {"output_per_million": 15.0}}},
            SUPPLEMENT_FIXTURE,
            {"alias_rules": [{"pattern": "^x$", "canonical": "y"}]},
        ):
            self.assertTrue(valid_supplement(payload), payload)

    def test_a_rules_only_supplement_counts_as_content(self):
        # Requiring a `pricing` block would silently discard a feed that only adds
        # alias rules or fast multipliers.
        self.assertTrue(has_supplement_content({"alias_rules": [{"pattern": "a", "canonical": "b"}]}))
        self.assertTrue(has_supplement_content({"fast_multipliers": {"m": 2.0}}))
        self.assertFalse(has_supplement_content({}))
        self.assertFalse(has_supplement_content({"updated_at": "2026-01-01T00:00:00Z"}))

    def test_a_poisoned_cached_supplement_cannot_break_construction(self):
        # The supplement cache is stored verbatim, so a hand-edited or truncated file
        # must degrade to the bundled prices rather than raising on every run.
        for bad in (
            {"pricing": {"m": {"input_per_million": None}}},
            {"fast_multipliers": {"gpt-5": "2x"}},
            {"pricing": [1]},
        ):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            cache = Path(tmp.name)
            (cache / "supplement.json").write_text(json.dumps(bad))
            store = ModelPricingStore(feeds=PricingFeeds(cache_dir=cache))
            self.assertIsNotNone(store.lookup("gpt-5"), bad)

    def test_a_poisoned_catalog_cache_is_ignored(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name)
        (cache / "litellm.json").write_text("{not json at all")
        store = ModelPricingStore(feeds=PricingFeeds(cache_dir=cache))
        self.assertIsNotNone(store.lookup("gpt-5.3-codex"))


class TestFreshnessPrecedence(unittest.TestCase):
    """A cached layer replaces the bundled one only when it is provably newer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_dir = Path(self.tmp.name)

    def _store(self):
        return ModelPricingStore(feeds=PricingFeeds(cache_dir=self.cache_dir))

    def _write(self, source, payload):
        (self.cache_dir / f"{source}.json").write_text(json.dumps(payload))

    def test_an_older_cached_supplement_does_not_win(self):
        # A leftover cache from a previous install must not keep overriding rates the
        # package now ships.
        self._write("supplement", {
            "updated_at": "2020-01-01T00:00:00Z",
            "pricing": {"gpt-5": {"input_per_million": 999.0, "output_per_million": 999.0}},
        })
        self.assertNotAlmostEqual(self._store().lookup("gpt-5").input_per_million, 999.0)

    def test_a_newer_cached_supplement_wins(self):
        self._write("supplement", {
            "updated_at": "2030-01-01T00:00:00Z",
            "pricing": {"gpt-5": {"input_per_million": 999.0, "output_per_million": 999.0}},
        })
        self.assertAlmostEqual(self._store().lookup("gpt-5").input_per_million, 999.0)

    def test_a_rules_only_cached_supplement_is_applied(self):
        self._write("supplement", {
            "updated_at": "2030-01-01T00:00:00Z",
            "alias_rules": [{"pattern": "^gateway-x$", "canonical": "claude-sonnet-4-5"}],
            "fast_multipliers": {"gateway-x": 2.0},
        })
        store = self._store()
        self.assertEqual(store.canonical_name("gateway-x"), "claude-sonnet-4-5")
        self.assertAlmostEqual(store.supplement.fast_multiplier("gateway-x"), 2.0)

    def test_an_older_cached_catalog_does_not_win(self):
        self._write("litellm", {
            "retrieved_at": "2020-01-01T00:00:00Z",
            "models": {"snapshot-only-model": {"i": 999.0, "o": 999.0}},
        })
        self.assertIsNone(self._store().lookup("snapshot-only-model"))

    def test_a_newer_cached_catalog_wins(self):
        self._write("litellm", {
            "retrieved_at": "2030-01-01T00:00:00Z",
            "models": {"snapshot-only-model": {"i": 4.0, "o": 8.0}},
        })
        rates = self._store().lookup("snapshot-only-model")
        self.assertIsNotNone(rates)
        self.assertAlmostEqual(rates.input_per_million, 4.0)

    def test_a_catalog_with_no_stamp_cannot_prove_it_is_newer(self):
        self._write("litellm", {"models": {"snapshot-only-model": {"i": 4.0, "o": 8.0}}})
        self.assertIsNone(self._store().lookup("snapshot-only-model"))


class TestBudgetAndThrottle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_dir = Path(self.tmp.name)
        self.clock = [1_000_000.0]

    def test_the_pass_cannot_outlast_its_budget(self):
        # Each attempt may consume a full timeout, so another attempt is only started
        # when one still fits. Otherwise the GNOME extension would kill the CLI and
        # the failure would never be recorded.
        calls = []

        def slow_fetch(url, etag):
            calls.append(url)
            self.clock[0] += TIMEOUT_SECONDS
            raise OSError("black hole")

        feeds = PricingFeeds(cache_dir=self.cache_dir, fetch=slow_fetch, now=lambda: self.clock[0])
        feeds.refresh()
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(TIMEOUT_SECONDS, TOTAL_BUDGET_SECONDS)
        # The sources that were not attempted stay due for the next run.
        self.assertIn("litellm", feeds.due_sources())

    def test_an_attempt_with_no_outcome_throttles_like_a_failure(self):
        # A process killed mid-fetch records the attempt but no outcome. Without this
        # the next run retries immediately and stalls again.
        (self.cache_dir / "state.json").write_text(json.dumps({"litellm": {"attempted_at": self.clock[0]}}))
        feeds = PricingFeeds(cache_dir=self.cache_dir, now=lambda: self.clock[0])
        self.assertNotIn("litellm", feeds.due_sources())
        self.clock[0] += FAILURE_RETRY_SECONDS + 1
        self.assertIn("litellm", feeds.due_sources())


if __name__ == "__main__":
    unittest.main()
