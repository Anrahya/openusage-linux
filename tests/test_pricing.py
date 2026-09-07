"""Unit tests for model pricing catalog, fuzzy matching, and cost calculations."""

import unittest

from openusage_linux.core.pricing import (
    ModelPricingStore,
    ModelRates,
    PricingCatalog,
)


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
        store = ModelPricingStore.get_shared()
        rate_gpt5 = store.rate_for("gpt-5.3-codex")
        self.assertIsNotNone(rate_gpt5)
        self.assertGreater(rate_gpt5.input_per_million, 0)
        self.assertGreater(rate_gpt5.output_per_million, 0)

    def test_new_gpt_and_opencode_models_have_rates(self):
        store = ModelPricingStore()
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
        store = ModelPricingStore()
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


if __name__ == "__main__":
    unittest.main()
