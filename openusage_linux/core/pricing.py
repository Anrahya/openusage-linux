"""Model Pricing Engine with fuzzy matching, snapshot loading, and dynamic updates."""

from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openusage_linux.core.pricing_feeds import PricingFeeds


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
    cache_read_is_explicit: bool = False
    cache_write_is_explicit: bool = False
    long_context_threshold_tokens: int = 200_000
    fast_multiplier: float = 1.0

    # 1-hour cache writes are billed at twice the input rate, matching Anthropic's
    # published prompt-caching multiplier and `cacheWrite1hInputMultiplier` in the
    # macOS app's ModelRates.swift.
    CACHE_WRITE_1H_INPUT_MULTIPLIER = 2.0

    def __post_init__(self):
        if self.cache_write_per_million is None or (self.cache_write_per_million == 0.0 and not self.cache_write_is_explicit):
            self.cache_write_per_million = self.input_per_million
        if self.cache_read_per_million is None or (self.cache_read_per_million == 0.0 and not self.cache_read_is_explicit):
            self.cache_read_per_million = self.input_per_million * 0.1

    @staticmethod
    def _selected_rate(base: float, long_context: Optional[float], use_long_context: bool) -> float:
        if use_long_context and long_context is not None:
            return long_context
        return base

    def cost_dollars(
        self,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        is_fast: bool = False,
        apply_long_context: bool = True,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
        prompt_tokens: Optional[int] = None,
        input_excludes_cached: bool = False,
    ) -> float:
        """Dollar cost of one request.

        ``input_tokens`` bills at the plain input rate. ``cache_write_tokens`` and
        ``cache_write_1h_tokens`` bill at the 5-minute write rate and at twice the
        input rate respectively, mirroring Anthropic's prompt-caching multipliers.
        ``prompt_tokens`` selects the long-context tier when a caller needs that
        decision to include cache tokens; it defaults to ``input_tokens``.

        ``input_excludes_cached`` describes the caller's ``input_tokens`` bucket. By
        default the bucket is treated as total input containing the cached portion,
        which is what the Codex and Cursor log shapes report, so the cached count is
        subtracted before billing at the input rate. Pass True when the caller
        already separates the buckets, as Claude and Grok logs do; otherwise a large
        cache read silently cancels out the plain input it never contained.
        """
        multiplier = self.fast_multiplier if is_fast else 1.0
        threshold_tokens = input_tokens if prompt_tokens is None else prompt_tokens
        use_long_context = apply_long_context and (threshold_tokens > self.long_context_threshold_tokens)

        input_rate = self._selected_rate(self.input_per_million, self.input_above_200k_per_million, use_long_context)
        output_rate = self._selected_rate(self.output_per_million, self.output_above_200k_per_million, use_long_context)
        cache_write_rate = self._selected_rate(self.cache_write_per_million, self.cache_write_above_200k_per_million, use_long_context)
        cache_read_rate = self._selected_rate(self.cache_read_per_million, self.cache_read_above_200k_per_million, use_long_context)
        cache_write_1h_rate = self._selected_rate(
            self.input_per_million * self.CACHE_WRITE_1H_INPUT_MULTIPLIER,
            self.input_above_200k_per_million * self.CACHE_WRITE_1H_INPUT_MULTIPLIER
            if self.input_above_200k_per_million is not None
            else None,
            use_long_context,
        )

        uncached_input = input_tokens if input_excludes_cached else max(0, input_tokens - cached_tokens)
        total_output = output_tokens + reasoning_tokens

        cost = (
            (uncached_input * input_rate / 1_000_000.0)
            + (cached_tokens * cache_read_rate / 1_000_000.0)
            + (total_output * output_rate / 1_000_000.0)
            + (cache_write_tokens * cache_write_rate / 1_000_000.0)
            + (cache_write_1h_tokens * cache_write_1h_rate / 1_000_000.0)
        )
        return cost * multiplier


def _optional_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rates_from_compact(entry: Any) -> Optional[ModelRates]:
    """Build rates from a pricing snapshot's compact schema.

    Mirrors `PricingCatalogCodecs.catalogFromCompact` in the macOS app: per-million
    rates where the cache write defaults to the input rate and the cache read to a
    tenth of it. Two fields carry provenance the plain numbers cannot:

    - ``cre`` is written only when the cache read was synthesized, so an absent flag
      means the rate was published. Without it a published zero cache read gets
      bumped to a tenth of input, overcharging models that offer no cache discount.
    - ``fast`` is the fast-variant multiplier, absent when the model has none.

    ``cw`` is always present and always published: the snapshot generator writes the
    input rate when a feed omits the cache-write price, so a zero there means the
    model genuinely charges nothing to write cache. Marking it explicit keeps
    `ModelRates.__post_init__` from substituting the input rate for that zero.
    """
    if not isinstance(entry, dict):
        return None
    input_rate = _optional_float(entry.get("i"))
    output_rate = _optional_float(entry.get("o"))
    if input_rate is None or output_rate is None:
        return None
    cache_write = _optional_float(entry.get("cw"))
    cache_read = _optional_float(entry.get("cr"))
    fast_multiplier = _optional_float(entry.get("fast"))
    return ModelRates(
        input_per_million=input_rate,
        output_per_million=output_rate,
        cache_write_per_million=input_rate if cache_write is None else cache_write,
        cache_read_per_million=input_rate * 0.1 if cache_read is None else cache_read,
        input_above_200k_per_million=_optional_float(entry.get("ia")),
        output_above_200k_per_million=_optional_float(entry.get("oa")),
        cache_write_above_200k_per_million=_optional_float(entry.get("cwa")),
        cache_read_above_200k_per_million=_optional_float(entry.get("cra")),
        cache_read_is_explicit=entry.get("cre") is not False,
        cache_write_is_explicit=cache_write is not None,
        fast_multiplier=1.0 if fast_multiplier is None else fast_multiplier,
    )


def compact_rates(models: Any) -> Dict[str, ModelRates]:
    """Turn a `{model: compact_entry}` mapping into rates, skipping unreadable rows."""
    if not isinstance(models, dict):
        return {}
    entries: Dict[str, ModelRates] = {}
    for key, value in models.items():
        rates = rates_from_compact(value)
        if rates is not None:
            entries[key] = rates
    return entries


def load_compact_snapshot(path: Path) -> Dict[str, ModelRates]:
    """Parse a bundled compact pricing snapshot. Unreadable files yield {}."""
    return load_compact_document(path)[1]


def load_compact_document(path: Path) -> Tuple[Optional[str], Dict[str, ModelRates]]:
    """Parse a compact snapshot once, returning its `retrieved_at` stamp and models.

    The stamp comes from the same read so comparing freshness does not re-parse the
    multi-megabyte catalog.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    stamp = data.get("retrieved_at")
    models = data.get("models", data)
    return (stamp if isinstance(stamp, str) else None), compact_rates(models)


def _is_newer(candidate: Any, reference: Any) -> bool:
    """Whether a fetched document's stamp is newer than the bundled one's.

    Both stamps are ISO-8601 UTC, so comparing them as strings orders them. A
    candidate with no stamp cannot prove it is newer, so it is not used; a missing
    reference means there is nothing to compare against, so the candidate wins.
    """
    if not isinstance(candidate, str) or not candidate:
        return False
    if not isinstance(reference, str) or not reference:
        return True
    return candidate > reference


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
                cache_write_is_explicit=cache_write is not None,
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
    def __init__(self, feeds: Optional[PricingFeeds] = None):
        self.catalog = PricingCatalog()
        self.supplement = PricingSupplement()
        self._feeds = feeds or PricingFeeds(cache_dir=self._get_cache_dir())
        self._lookup_memo: Dict[Tuple[str, bool], Optional[ModelRates]] = {}
        self._canonical_memo: Dict[str, str] = {}
        self._load_bundled()

    @classmethod
    def get_shared(cls) -> ModelPricingStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_data_dir(self) -> Path:
        return Path(__file__).parent.parent / "data" / "pricing"

    @property
    def cache_dir(self) -> Path:
        """Where the fetched feed caches live."""
        return self._feeds.cache_dir

    def _get_cache_dir(self) -> Path:
        path = Path.home() / ".cache" / "openusage" / "pricing"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_bundled(self):
        """Rebuild the catalog from bundled data plus any fetched feed caches.

        Layer order matters and is the same whether a layer came from the package or
        from a runtime fetch: models.dev is the base, litellm overrides it, and the
        supplement overrides both. A fetched catalog is applied at its own layer
        rather than on top of everything, so a live catalog cannot silently outrank a
        deliberate supplement override.

        A cached layer is used only when it is newer than the bundled copy it would
        replace. Without that comparison a leftover cache from an earlier install
        would keep overriding freshly shipped rates, and someone offline or behind a
        proxy would never pick up an upgrade.
        """
        data_dir = self._get_data_dir()
        cached = self._feeds.load_cached()

        entries: Dict[str, ModelRates] = {}
        self.supplement = PricingSupplement()

        for source, filename in (
            ("models.dev", "pricing_models_dev_snapshot.json"),
            ("litellm", "pricing_litellm_snapshot.json"),
        ):
            bundled_stamp, bundled_models = load_compact_document(data_dir / filename)
            entries.update(bundled_models)
            cached_doc = cached.get(source)
            if cached_doc and _is_newer(cached_doc.get("retrieved_at"), bundled_stamp):
                entries.update(compact_rates(cached_doc.get("models")))

        supp_file = data_dir / "pricing_supplement.json"
        if supp_file.exists():
            try:
                with open(supp_file, "r", encoding="utf-8") as f:
                    self.supplement = PricingSupplement.from_dict(json.load(f))
            except Exception:
                self.supplement = PricingSupplement()

        # Merge supplement direct pricing (highest precedence)
        for k, v in self.supplement.pricing.items():
            entries[k] = v

        # Add sensible default fallbacks for common OpenAI / Codex models if missing
        defaults = {
            "gpt-5": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-5-codex": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-5.3-codex": ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25),
            "gpt-5.3-codex-spark": ModelRates(input_per_million=1.5, output_per_million=6.0, cache_read_per_million=0.75),
            "gpt-6-astra": ModelRates(input_per_million=10.0, output_per_million=50.0, cache_read_per_million=1.0, cache_write_per_million=12.5),
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

        # A fetched supplement is applied last, and only when it is newer than the
        # bundled one, because it also contributes alias rules and fast multipliers.
        cached_supplement = cached.get("supplement")
        if cached_supplement and _is_newer(cached_supplement.get("updated_at"), self.supplement.updated_at):
            try:
                self._apply_supplement(PricingSupplement.from_dict(cached_supplement))
            except Exception:
                # The bundled supplement is already loaded, so a malformed cache must
                # degrade to slightly stale prices rather than break construction.
                pass

        self._clear_memos()

    def _apply_supplement(self, supplement: PricingSupplement) -> None:
        for key, rates in supplement.pricing.items():
            self.catalog.entries[key] = rates
        if supplement.fast_multipliers:
            merged = dict(self.supplement.fast_multipliers)
            merged.update(supplement.fast_multipliers)
            self.supplement.fast_multipliers = merged
        if supplement.alias_rules:
            self.supplement.alias_rules = self._merged_alias_rules(supplement.alias_rules)
        if supplement.updated_at:
            self.supplement.updated_at = supplement.updated_at
        self._clear_memos()

    def _merged_alias_rules(self, incoming: List[AliasRule]) -> List[AliasRule]:
        """Prepend `incoming`, keeping the first rule for any duplicate pattern.

        A startup cache load and an in-process refresh can both apply the same
        supplement, which would otherwise stack the same rules twice. Lookups only
        read the first match, so this is correctness-neutral and cheap.
        """
        merged: List[AliasRule] = []
        seen = set()
        for rule in list(incoming) + list(self.supplement.alias_rules):
            key = (rule.pattern.pattern, rule.canonical)
            if key in seen:
                continue
            seen.add(key)
            merged.append(rule)
        return merged

    def sync_pricing_feeds(self, force: bool = False) -> Dict[str, bool]:
        """Refresh the due pricing feeds and rebuild the catalog. Never raises.

        Bounded to one fetch per source per hour, with a 30-minute retry after a
        failure and ETag revalidation, mirroring the macOS app. Rebuilding rather than
        patching in place keeps the layer order intact however many times this runs.
        """
        changed = self._feeds.refresh(force=force)
        if any(changed.values()):
            self._load_bundled()
        return changed

    def _with_fast_multiplier(self, rates: ModelRates, canonical: str) -> ModelRates:
        mult = self.supplement.fast_multiplier(canonical)
        if mult and mult != 1.0:
            return replace(rates, fast_multiplier=mult)
        return rates

    def lookup(self, model: str, is_fast: bool = False) -> Optional[ModelRates]:
        """Resolve a model to real published rates, or None when it is unpriced.

        Mirrors `ModelPricing.resolve` in the macOS app, which returns nil for a
        model no catalog knows. Prefer this over `rate_for` wherever a wrong number
        would be worse than a missing one: the caller can then exclude the tokens
        from cost and report the model, instead of billing a plausible fiction.

        Results are memoized per name, also as upstream does. Without that, a busy
        transcript re-runs the full fuzzy sweep of the catalog once per log line,
        because most lines repeat a handful of model names.
        """
        key = (model, is_fast)
        if key in self._lookup_memo:
            return self._lookup_memo[key]
        rates = self._lookup_uncached(model, is_fast)
        self._lookup_memo[key] = rates
        return rates

    def _lookup_uncached(self, model: str, is_fast: bool) -> Optional[ModelRates]:
        canonical = self.canonical_name(model)

        exact = self.catalog.find_exact(canonical)
        if exact:
            return self._with_fast_multiplier(exact[1], canonical)

        fuzzy = self.catalog.find_fuzzy(canonical)
        if fuzzy:
            return self._with_fast_multiplier(fuzzy[1], canonical)

        return None

    def canonical_name(self, model: str) -> str:
        """The alias rule's target, or the name itself. Memoized like upstream's
        `canonicalName(for:)`, since the alias list is scanned linearly per call."""
        if model in self._canonical_memo:
            return self._canonical_memo[model]
        canonical = self.supplement.canonical_name(model) or model
        self._canonical_memo[model] = canonical
        return canonical

    def rate_for(self, model: str, is_fast: bool = False) -> ModelRates:
        """Resolve a model, falling back to a generic rate when nothing matches.

        The fallback is a GPT-5-class estimate, not a guess at the real price, so the
        number it produces is meaningless for the model it is applied to. Callers that
        report cost to a user should use `lookup` and handle None instead.
        """
        rates = self.lookup(model, is_fast=is_fast)
        if rates is not None:
            return rates
        return ModelRates(input_per_million=2.5, output_per_million=10.0, cache_read_per_million=1.25)

    def _clear_memos(self) -> None:
        """Drop memoized resolutions. Any change to catalog or alias rules must call
        this, or a model resolved before a pricing refresh keeps its stale rate."""
        self._lookup_memo.clear()
        self._canonical_memo.clear()

    def cost_for(
        self,
        model: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        is_fast: bool = False,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
        prompt_tokens: Optional[int] = None,
        input_excludes_cached: bool = False,
    ) -> float:
        rate = self.rate_for(model, is_fast=is_fast)
        return rate.cost_dollars(
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            is_fast=is_fast,
            cache_write_tokens=cache_write_tokens,
            cache_write_1h_tokens=cache_write_1h_tokens,
            prompt_tokens=prompt_tokens,
            input_excludes_cached=input_excludes_cached,
        )
