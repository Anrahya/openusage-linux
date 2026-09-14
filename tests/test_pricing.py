"""Unit tests for model pricing catalog, fuzzy matching, and cost calculations."""

import shutil
import tempfile
import unittest
from pathlib import Path

from openusage_linux.core.pricing import (
    ModelPricingStore,
    ModelRates,
    PricingCatalog,
    rates_from_compact,
)
from openusage_linux.core.pricing_feeds import PricingFeeds


def isolated_store(case: unittest.TestCase) -> ModelPricingStore:
    """A store that reads only the packaged data.

    The real store layers whatever pricing feeds this machine has fetched over the
    bundled snapshots, so a test that used it would pass or fail depending on
    someone's cache directory.
    """
    tmp = tempfile.mkdtemp(prefix="openusage-pricing-")
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    return ModelPricingStore(feeds=PricingFeeds(cache_dir=Path(tmp)))


class TestPricing(unittest.TestCase):
    def test_fuzzy_model_matching(self):
        catalog = PricingCatalog(
            entries={
                "claude-3-5-sonnet-20241022": ModelRates(input_per_million=3.0, output_per_million=15.0),
                "claude-sonnet-4": ModelRates(input_per_million=3.0, output_per_million=15.0),
                "gpt-4o": ModelRates(input_per_million=2.5, output_per_million=10.0),
                "xai/grok-2": ModelRates(input_per_million=2.0, output_per_million=10.0),
            }
        )

        # Exact match
        exact = catalog.find_exact("gpt-4o")
        self.assertIsNotNone(exact)
        self.assertEqual(exact[0], "gpt-4o")

        # Separator normalization (`.` to `-`)
        fuzzy_grok = catalog.find_fuzzy("grok.2")
        self.assertIsNotNone(fuzzy_grok)
        self.assertEqual(fuzzy_grok[0], "xai/grok-2")

        # Suffix version rejection rule: `claude-sonnet-4` should NOT match `claude-sonnet-4-5`
        fuzzy_v5 = catalog.find_fuzzy("claude-sonnet-4-5")
        self.assertIsNone(fuzzy_v5)

        # Date suffix allowed: `claude-sonnet-4-20250514` matches `claude-sonnet-4`
        fuzzy_date = catalog.find_fuzzy("claude-sonnet-4-20250514")
        self.assertIsNotNone(fuzzy_date)
        self.assertEqual(fuzzy_date[0], "claude-sonnet-4")

    def test_cost_calculation(self):
        rates = ModelRates(
            input_per_million=2.5,      # $2.50 / 1M
            output_per_million=10.0,    # $10.00 / 1M
            cache_read_per_million=1.25 # $1.25 / 1M
        )

        # 1M input (with 500k cached), 100k output
        # uncached input: 500k -> 500,000 * 2.5 / 1,000,000 = $1.25
        # cached input: 500k -> 500,000 * 1.25 / 1,000,000 = $0.625
        # output: 100k -> 100,000 * 10.0 / 1,000,000 = $1.00
        # total = $1.25 + $0.625 + $1.00 = $2.875
        cost = rates.cost_dollars(
            input_tokens=1_000_000,
            cached_tokens=500_000,
            output_tokens=100_000,
            reasoning_tokens=0,
            is_fast=False,
        )
        self.assertAlmostEqual(cost, 2.875, places=3)

    def test_pricing_store_resolves_common_models(self):
        store = isolated_store(self)
        rate_gpt5 = store.rate_for("gpt-5.3-codex")
        self.assertIsNotNone(rate_gpt5)
        self.assertGreater(rate_gpt5.input_per_million, 0)
        self.assertGreater(rate_gpt5.output_per_million, 0)

    def test_new_gpt_and_opencode_models_have_rates(self):
        store = isolated_store(self)
        astra = store.rate_for("gpt-6-astra")
        self.assertAlmostEqual(astra.input_per_million, 10.0)
        self.assertAlmostEqual(astra.output_per_million, 50.0)
        self.assertAlmostEqual(store.rate_for("gpt-6").input_per_million, 10.0)
        self.assertAlmostEqual(store.rate_for("gpt-6-astra-high").input_per_million, 10.0)
        self.assertEqual(store.supplement.canonical_name("GPT-6 Astra (Auto Balanced)"), "gpt-6-astra")

        sol = store.rate_for("gpt-5.6")
        self.assertAlmostEqual(sol.input_per_million, 4.0)
        self.assertAlmostEqual(sol.output_per_million, 20.0)

        fable = store.rate_for("claude-fable-5-1")
        self.assertAlmostEqual(fable.input_per_million, 10.0)
        self.assertAlmostEqual(fable.output_per_million, 50.0)
        gemini = store.rate_for("gemini-3.8-flash")
        self.assertAlmostEqual(gemini.input_per_million, 0.75)
        flash = store.rate_for("glm-5.3-flash")
        self.assertAlmostEqual(flash.input_per_million, 0.15)
        muse = store.rate_for("muse-spark-1.3")
        self.assertAlmostEqual(muse.input_per_million, 1.25)
        qwen = store.rate_for("qwen3.7-max")
        self.assertAlmostEqual(qwen.output_per_million, 7.50)

    def test_rate_for_does_not_mutate_catalog_entry(self):
        store = isolated_store(self)
        store.catalog.entries["mut-test"] = ModelRates(
            input_per_million=2.5,
            output_per_million=10.0,
            cache_read_per_million=1.25,
        )
        store.supplement.fast_multipliers["mut-test"] = 2.0

        original = store.catalog.entries["mut-test"].fast_multiplier
        fast = store.rate_for("mut-test", is_fast=True)
        again = store.rate_for("mut-test", is_fast=False)

        self.assertEqual(original, 1.0)
        self.assertEqual(fast.fast_multiplier, 2.0)
        self.assertEqual(again.fast_multiplier, 2.0)
        self.assertIsNot(fast, store.catalog.entries["mut-test"])
        self.assertEqual(store.catalog.entries["mut-test"].fast_multiplier, original)

    def test_explicit_zero_cache_read_is_preserved(self):
        rates = ModelRates(
            input_per_million=2.5,
            output_per_million=10.0,
            cache_read_per_million=0.0,
            cache_read_is_explicit=True,
        )
        self.assertEqual(rates.cache_read_per_million, 0.0)


class TestCacheWriteBilling(unittest.TestCase):
    """Anthropic bills cache writes above the input rate; the engine used to ignore it."""

    def test_every_bucket_bills_at_its_published_rate(self):
        rates = ModelRates(
            input_per_million=3.0,
            output_per_million=15.0,
            cache_write_per_million=3.75,
            cache_read_per_million=0.30,
        )
        cost = rates.cost_dollars(
            input_tokens=1_000_000,
            cached_tokens=2_000_000,
            output_tokens=1_000_000,
            cache_write_tokens=1_000_000,
            cache_write_1h_tokens=1_000_000,
            input_excludes_cached=True,
            apply_long_context=False,
        )
        # 3.00 input + 0.60 cache read + 15.00 output + 3.75 5m write + 6.00 1h write
        self.assertAlmostEqual(cost, 28.35, places=6)

    def test_one_hour_writes_bill_at_twice_the_input_rate(self):
        rates = ModelRates(input_per_million=3.0, output_per_million=15.0, cache_write_per_million=3.75)
        five_minutes = rates.cost_dollars(
            input_tokens=0, cached_tokens=0, output_tokens=0,
            cache_write_tokens=1_000_000, apply_long_context=False,
        )
        one_hour = rates.cost_dollars(
            input_tokens=0, cached_tokens=0, output_tokens=0,
            cache_write_1h_tokens=1_000_000, apply_long_context=False,
        )
        self.assertAlmostEqual(five_minutes, 3.75, places=6)
        self.assertAlmostEqual(one_hour, 6.00, places=6)

    def test_omitting_the_new_arguments_reproduces_the_previous_cost(self):
        rates = ModelRates(
            input_per_million=2.5,
            output_per_million=10.0,
            cache_read_per_million=1.25,
        )
        cost = rates.cost_dollars(
            input_tokens=1_000_000,
            cached_tokens=500_000,
            output_tokens=100_000,
            reasoning_tokens=0,
            is_fast=False,
        )
        self.assertAlmostEqual(cost, 2.875, places=3)

    def test_input_excludes_cached_skips_the_subtraction(self):
        rates = ModelRates(input_per_million=3.0, output_per_million=15.0, cache_read_per_million=0.30)
        # Default shape: input is a total that contains the cached portion.
        total_shape = rates.cost_dollars(
            input_tokens=1_000_000, cached_tokens=5_000_000, output_tokens=0,
            apply_long_context=False,
        )
        # Separate buckets, as Claude and Grok logs report them. A big cache read must
        # not cancel out plain input it never contained.
        separate_shape = rates.cost_dollars(
            input_tokens=1_000_000, cached_tokens=5_000_000, output_tokens=0,
            apply_long_context=False, input_excludes_cached=True,
        )
        self.assertAlmostEqual(total_shape, 1.50, places=6)
        self.assertAlmostEqual(separate_shape, 4.50, places=6)

    def test_prompt_tokens_selects_the_long_context_tier(self):
        rates = ModelRates(
            input_per_million=3.0,
            output_per_million=15.0,
            input_above_200k_per_million=6.0,
        )
        under = rates.cost_dollars(input_tokens=100_000, cached_tokens=0, output_tokens=0)
        over = rates.cost_dollars(
            input_tokens=100_000, cached_tokens=0, output_tokens=0, prompt_tokens=250_000,
        )
        self.assertAlmostEqual(under, 0.30, places=6)
        self.assertAlmostEqual(over, 0.60, places=6)

    def test_real_sonnet_rates_match_the_published_table(self):
        store = isolated_store(self)
        rates = store.lookup("claude-sonnet-4-5")
        self.assertAlmostEqual(rates.input_per_million, 3.0)
        self.assertAlmostEqual(rates.output_per_million, 15.0)
        self.assertAlmostEqual(rates.cache_read_per_million, 0.30)
        self.assertAlmostEqual(rates.cache_write_per_million, 3.75)
        one_hour = rates.cost_dollars(
            input_tokens=0, cached_tokens=0, output_tokens=0,
            cache_write_1h_tokens=100_000, apply_long_context=False,
        )
        self.assertAlmostEqual(one_hour, 0.60, places=6)

    def test_fable_cache_read_uses_the_documented_lower_multiplier(self):
        store = isolated_store(self)
        rates = store.lookup("claude-fable-5.1")
        # Fable 5.1 bills cache reads at 0.025x input, not the standard 0.1x.
        self.assertAlmostEqual(rates.input_per_million, 10.0)
        self.assertAlmostEqual(rates.cache_read_per_million, 0.25)


class TestUnpricedModels(unittest.TestCase):
    def test_lookup_reports_unpriced_models_instead_of_inventing_a_rate(self):
        store = isolated_store(self)
        self.assertIsNone(store.lookup("totally-unknown-model-x"))
        # `rate_for` keeps its documented estimate for callers that cannot show a gap.
        fallback = store.rate_for("totally-unknown-model-x")
        self.assertAlmostEqual(fallback.input_per_million, 2.5)
        self.assertAlmostEqual(fallback.cache_read_per_million, 1.25)

    def test_lookup_memoizes_but_records_an_unpriced_name_as_none(self):
        store = isolated_store(self)
        first = store.lookup("gpt-5.3-codex")
        self.assertIsNotNone(first)
        self.assertIs(store.lookup("gpt-5.3-codex"), first)
        self.assertIsNone(store.lookup("another-unknown-model"))
        self.assertIsNone(store.lookup("another-unknown-model"))


class TestCompactSchema(unittest.TestCase):
    def test_cre_and_fast_survive_the_round_trip(self):
        rates = rates_from_compact({"i": 5.0, "o": 25.0, "cw": 6.25, "cr": 0.0, "fast": 2.0})
        self.assertEqual(rates.cache_read_per_million, 0.0)
        self.assertTrue(rates.cache_read_is_explicit)
        self.assertAlmostEqual(rates.fast_multiplier, 2.0)

        synthesized = rates_from_compact({"i": 5.0, "o": 25.0, "cw": 6.25, "cr": 0.5, "cre": False})
        self.assertFalse(synthesized.cache_read_is_explicit)
        self.assertAlmostEqual(synthesized.fast_multiplier, 1.0)

    def test_unreadable_entries_are_skipped(self):
        self.assertIsNone(rates_from_compact("not-a-dict"))
        self.assertIsNone(rates_from_compact({"i": 1.0}))
        self.assertIsNone(rates_from_compact({"i": "abc", "o": 2.0}))

    def test_a_published_zero_cache_write_stays_free(self):
        # The snapshot generator writes the input rate when a feed omits the
        # cache-write price, so a zero in the compact schema means the model really
        # charges nothing to write cache. Substituting the input rate would invent a
        # charge that no published rate has.
        rates = rates_from_compact({"i": 0.435, "o": 0.87, "cw": 0.0, "cr": 0.0036})
        self.assertEqual(rates.cache_write_per_million, 0.0)
        self.assertTrue(rates.cache_write_is_explicit)

    def test_a_missing_cache_write_falls_back_to_the_input_rate(self):
        rates = rates_from_compact({"i": 2.0, "o": 8.0})
        self.assertAlmostEqual(rates.cache_write_per_million, 2.0)
        self.assertFalse(rates.cache_write_is_explicit)


if __name__ == "__main__":
    unittest.main()
