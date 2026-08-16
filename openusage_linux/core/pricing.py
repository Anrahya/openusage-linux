"""Model Pricing Engine with fuzzy matching, snapshot loading, and dynamic updates."""

from __future__ import annotations
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class ModelRates:
    input_per_million: float
    output_per_million: float
    cache_write_per_million: float = 0.0
    cache_read_per_million: float = 0.0
    input_above_200k_per_million: Optional[float] = None
    output_above_200k_per_million: Optional[float] = None
    cache_write_above_200k_per_million: Optional[float] = None
    cache_read_above_200k_per_million: Optional[float] = None
    cache_read_is_explicit: bool = True
    long_context_threshold_tokens: int = 200_000
    fast_multiplier: float = 1.0

    def __post_init__(self):
        if not self.cache_write_per_million:
            self.cache_write_per_million = self.input_per_million
        if not self.cache_read_per_million:
            self.cache_read_per_million = self.input_per_million * 0.1

    def cost_dollars(
        self,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        is_fast: bool = False,
        apply_long_context: bool = True,
    ) -> float:
        multiplier = self.fast_multiplier if is_fast else 1.0
        prompt_tokens = input_tokens
        use_long_context = apply_long_context and (prompt_tokens > self.long_context_threshold_tokens)

        input_rate = (self.input_above_200k_per_million if use_long_context and self.input_above_200k_per_million is not None else self.input_per_million)
        output_rate = (self.output_above_200k_per_million if use_long_context and self.output_above_200k_per_million is not None else self.output_per_million)
        cache_read_rate = (self.cache_read_above_200k_per_million if use_long_context and self.cache_read_above_200k_per_million is not None else self.cache_read_per_million)

        uncached_input = max(0, input_tokens - cached_tokens)
        total_output = output_tokens + reasoning_tokens

        cost = (
            (uncached_input * input_rate / 1_000_000.0)
            + (cached_tokens * cache_read_rate / 1_000_000.0)
            + (total_output * output_rate / 1_000_000.0)
        )
        return cost * multiplier


class PricingCatalog:
    def __init__(self, entries: Optional[Dict[str, ModelRates]] = None, retrieved_at: Optional[str] = None):
        self.entries: Dict[str, ModelRates] = entries or {}
        self.retrieved_at = retrieved_at

    def find_exact(self, model: str) -> Optional[Tuple[str, ModelRates]]:
        if model in self.entries:
            return (model, self.entries[model])
        return None

    def find_fuzzy(self, model: str) -> Optional[Tuple[str, ModelRates]]:
        normalized_model = self.normalize_key(model)
        best: Optional[Tuple[str, ModelRates]] = None

        for key, rates in self.entries.items():
            if self.key_matches(candidate=key, model=model, normalized_model=normalized_model):
                if best is None:
                    best = (key, rates)
                else:
                    best_key, _ = best
                    if len(key) > len(best_key) or (len(key) == len(best_key) and key < best_key):
                        best = (key, rates)
        return best

    @staticmethod
    def normalize_key(value: str) -> str:
        return value.replace(".", "-").replace("@", "-")

    @classmethod
    def key_matches(cls, candidate: str, model: str, normalized_model: str) -> bool:
        if cls.contains_key(model, candidate) or cls.contains_key(candidate, model):
            return True
        norm_candidate = cls.normalize_key(candidate)
        return cls.contains_key(normalized_model, norm_candidate) or cls.contains_key(norm_candidate, normalized_model)

    @classmethod
    def contains_key(cls, value: str, key: str) -> bool:
        if not key or len(key) > len(value):
            return False
        
        pos = 0
        while True:
            idx = value.find(key, pos)
            if idx == -1:
                return False
            
            before_ok = (idx == 0) or (not value[idx - 1].isalnum())
            if before_ok:
                suffix = value[idx + len(key):]
                if cls.suffix_allows_match(key, suffix):
                    return True
            pos = idx + 1

    @classmethod
    def suffix_allows_match(cls, key: str, suffix: str) -> bool:
        if not suffix:
            return True
        separator = suffix[0]
        if separator.isalnum():
            return False
        return not cls.suffix_starts_with_numeric_model_version(key, suffix)

    @classmethod
    def suffix_starts_with_numeric_model_version(cls, key: str, suffix: str) -> bool:
        if not key or not key[-1].isdigit():
            return False
        if not suffix or suffix[0] not in ("-", "."):
            return False
        
        rest = suffix[1:]
        digits = []
        for ch in rest:
            if ch.isdigit():
                digits.append(ch)
            else:
                break
        
        digit_count = len(digits)
        if digit_count == 0:
            return False
        
        # An 8-digit date suffix e.g. -20260415 is allowed as a date, not a version increment
        after_digits = rest[digit_count:digit_count + 1]
        is_date_suffix = (digit_count == 8) and (not after_digits or not after_digits.isalnum())
        return not is_date_suffix

    def merging(self, other: PricingCatalog) -> PricingCatalog:
        merged = dict(self.entries)
        merged.update(other.entries)
        return PricingCatalog(entries=merged, retrieved_at=other.retrieved_at or self.retrieved_at)


@dataclass
class AliasRule:
    pattern: re.Pattern
    canonical: str


class PricingSupplement:
    def __init__(
        self,
        pricing: Optional[Dict[str, ModelRates]] = None,
        fast_multipliers: Optional[Dict[str, float]] = None,
        alias_rules: Optional[List[AliasRule]] = None,
        updated_at: Optional[str] = None,
    ):
        self.pricing = pricing or {}
        self.fast_multipliers = fast_multipliers or {}
        self.alias_rules = alias_rules or []
        self.updated_at = updated_at

    def canonical_name(self, model: str) -> Optional[str]:
        for rule in self.alias_rules:
            if rule.pattern.search(model):
                return rule.canonical
        return None

    def fast_multiplier(self, model: str) -> Optional[float]:
        if model in self.fast_multipliers:
            return self.fast_multipliers[model]
        normalized = PricingCatalog.normalize_key(model)
        for part in re.split(r"[/:]", normalized):
            for base, multiplier in self.fast_multipliers.items():
                norm_base = PricingCatalog.normalize_key(base)
                if part == norm_base or part.endswith("-" + norm_base) or part.startswith(norm_base + "-"):
                    return multiplier
        return None

    @classmethod
    def from_dict(cls, data: dict) -> PricingSupplement:
        pricing: Dict[str, ModelRates] = {}
        fast_mults = data.get("fast_multipliers", {}) or {}

        for model, entry in data.get("pricing", {}).items():
            input_val = float(entry.get("input_per_million", 0.0))
            output_val = float(entry.get("output_per_million", 0.0))
            cache_write = entry.get("cache_write_per_million")
            cache_read = entry.get("cache_read_per_million")

            pricing[model] = ModelRates(
                input_per_million=input_val,
                output_per_million=output_val,
                cache_write_per_million=float(cache_write) if cache_write is not None else input_val,
                cache_read_per_million=float(cache_read) if cache_read is not None else (input_val * 0.1),
                cache_read_is_explicit=cache_read is not None,
                fast_multiplier=float(fast_mults.get(model, 1.0)),
            )

        rules: List[AliasRule] = []
        for r in data.get("alias_rules", []):
            try:
                pattern = re.compile(r["pattern"])
                rules.append(AliasRule(pattern=pattern, canonical=r["canonical"]))
            except Exception:
                continue

        return cls(
            pricing=pricing,
            fast_multipliers={k: float(v) for k, v in fast_mults.items()},
            alias_rules=rules,
            updated_at=data.get("updated_at"),
        )


class ModelPricingStore:
    _instance: Optional[ModelPricingStore] = None
    REMOTE_SUPPLEMENT_URL = "https://robinebers.github.io/openusage/pricing_supplement.json"

    def __init__(self):
        self.catalog = PricingCatalog()
        self.supplement = PricingSupplement()
        self._load_bundled()
        self._load_cached_remote()

    @classmethod
    def get_shared(cls) -> ModelPricingStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_data_dir(self) -> Path:
        return Path(__file__).parent.parent / "data" / "pricing"

    def _get_cache_dir(self) -> Path:
        path = Path.home() / ".cache" / "openusage" / "pricing"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_bundled(self):
        data_dir = self._get_data_dir()
        supp_file = data_dir / "pricing_supplement.json"
        lite_file = data_dir / "pricing_litellm_snapshot.json"
        dev_file = data_dir / "pricing_models_dev_snapshot.json"

        # Load supplement
        if supp_file.exists():
            try:
                with open(supp_file, "r", encoding="utf-8") as f:
                    self.supplement = PricingSupplement.from_dict(json.load(f))
            except Exception:
                pass

        entries: Dict[str, ModelRates] = {}

        # Load models.dev snapshot
        if dev_file.exists():
            try:
                with open(dev_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    models_dict = data.get("models", data)
                    for k, v in models_dict.items():
                        entries[k] = ModelRates(
                            input_per_million=float(v.get("i", 0.0)),
                            output_per_million=float(v.get("o", 0.0)),
                            cache_write_per_million=float(v.get("cw", v.get("i", 0.0))),
                            cache_read_per_million=float(v.get("cr", float(v.get("i", 0.0)) * 0.1)),
                            input_above_200k_per_million=float(v["ia"]) if "ia" in v else None,
                            output_above_200k_per_million=float(v["oa"]) if "oa" in v else None,
                        )
            except Exception:
                pass

        # Load litellm snapshot
        if lite_file.exists():
            try:
                with open(lite_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    models_dict = data.get("models", data)
                    for k, v in models_dict.items():
                        entries[k] = ModelRates(
                            input_per_million=float(v.get("i", 0.0)),
                            output_per_million=float(v.get("o", 0.0)),
                            cache_write_per_million=float(v.get("cw", v.get("i", 0.0))),
                            cache_read_per_million=float(v.get("cr", float(v.get("i", 0.0)) * 0.1)),
                            input_above_200k_per_million=float(v["ia"]) if "ia" in v else None,
                            output_above_200k_per_million=float(v["oa"]) if "oa" in v else None,
                            cache_write_above_200k_per_million=float(v["cwa"]) if "cwa" in v else None,
                            cache_read_above_200k_per_million=float(v["cra"]) if "cra" in v else None,
                        )
            except Exception:
                pass

        # Merge supplement direct pricing (highest precedence)
        for k, v in self.supplement.pricing.items():
            entries[k] = v

        # Add sensible default fallbacks for common OpenAI / Codex models if missing
        defaults = {
            "gpt-5": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-5-codex": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-5.3-codex": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-5.3-codex-spark": ModelRates(input_per_million=1.5, output_per_million=6.0, cache_read_per_million=0.75),
            "o3": ModelRates(input_per_million=5.0, output_per_million=20.0, cache_read_per_million=2.5),
            "o3-mini": ModelRates(input_per_million=1.1, output_per_million=4.4, cache_read_per_million=0.55),
            "o1": ModelRates(input_per_million=15.0, output_per_million=60.0, cache_read_per_million=7.5),
            "gpt-4o": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-4o-mini": ModelRates(input_per_million=0.15, output_per_million=0.6, cache_read_per_million=0.075),
        }
        for k, v in defaults.items():
            if k not in entries:
                entries[k] = v

        self.catalog = PricingCatalog(entries=entries)

    def _load_cached_remote(self):
        cache_file = self._get_cache_dir() / "remote_supplement.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_supp = PricingSupplement.from_dict(json.load(f))
                    for k, v in cached_supp.pricing.items():
                        self.catalog.entries[k] = v
            except Exception:
                pass

    def sync_remote_supplement(self):
        try:
            req = urllib.request.Request(
                self.REMOTE_SUPPLEMENT_URL,
                headers={"User-Agent": "OpenUsage-Linux"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    cached_supp = PricingSupplement.from_dict(data)
                    for k, v in cached_supp.pricing.items():
                        self.catalog.entries[k] = v
                    cache_file = self._get_cache_dir() / "remote_supplement.json"
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f)
        except Exception:
            pass

    def rate_for(self, model: str, is_fast: bool = False) -> ModelRates:
        canonical = self.supplement.canonical_name(model) or model
        
        # 1. Exact match
        exact = self.catalog.find_exact(canonical)
        if exact:
            rates = exact[1]
            mult = self.supplement.fast_multiplier(canonical)
            if mult and mult != 1.0:
                rates.fast_multiplier = mult
            return rates

        # 2. Fuzzy match
        fuzzy = self.catalog.find_fuzzy(canonical)
        if fuzzy:
            rates = fuzzy[1]
            mult = self.supplement.fast_multiplier(canonical)
            if mult and mult != 1.0:
                rates.fast_multiplier = mult
            return rates

        # 3. Fallback rate
        return ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25)

    def cost_for(
        self,
        model: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        is_fast: bool = False,
    ) -> float:
        rate = self.rate_for(model, is_fast=is_fast)
        return rate.cost_dollars(
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            is_fast=is_fast,
        )
