"""Runtime refresh for the three pricing feeds.

The bundled snapshots are a build-time artifact, frozen at whatever release they
shipped in, so without this the app can never price a model that appeared after
that release. The macOS app refreshes all three feeds hourly and keeps a
per-source disk cache; this mirrors `ModelPricingStore.swift`, including the
ETag revalidation that makes most hourly checks a 304 with no body.

`compact_from_litellm` and `compact_from_models_dev` are the same transform as
the Python block in the upstream `script/update_pricing_snapshots.sh`, which
generates the bundled snapshots. Keeping one transform means the runtime path and
the committed snapshot cannot drift into different shapes.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openusage_linux.core.atomic import atomic_write_json

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
MODELS_DEV_URL = "https://models.dev/api.json"
SUPPLEMENT_URL = "https://robinebers.github.io/openusage/pricing_supplement.json"

SOURCE_URLS: Dict[str, str] = {
    "models.dev": MODELS_DEV_URL,
    "litellm": LITELLM_URL,
    "supplement": SUPPLEMENT_URL,
}

# Apply order matters: litellm wins wherever both catalogs know a name, and the
# supplement overrides both.
SOURCE_ORDER: Tuple[str, ...] = ("models.dev", "litellm", "supplement")

REFRESH_INTERVAL_SECONDS = 60 * 60
FAILURE_RETRY_SECONDS = 30 * 60
TIMEOUT_SECONDS = 12
# A refresh blocks the caller, and the GNOME extension kills the CLI after 20
# seconds, so the whole pass is bounded rather than per source. On a healthy
# connection all three feeds finish in a couple of seconds; on a black-holed
# network this stops after the first timeout and keeps the cached data.
TOTAL_BUDGET_SECONDS = 15
# LiteLLM's feed is about 2.3 MB today. The old 2 MB guard would have rejected it.
MAX_SOURCE_BYTES = 8_000_000


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _per_million(value: Any) -> Optional[float]:
    number = _number(value)
    return None if number is None else number * 1e6


def _compact_model(
    input_pm: float,
    output_pm: float,
    cache_write_pm: float,
    cache_read_pm: float,
    cache_read_explicit: bool,
    ia: Optional[float] = None,
    oa: Optional[float] = None,
    cwa: Optional[float] = None,
    cra: Optional[float] = None,
    fast: Optional[float] = None,
) -> Dict[str, Any]:
    model: Dict[str, Any] = {
        "i": input_pm,
        "o": output_pm,
        "cw": cache_write_pm,
        "cr": cache_read_pm,
    }
    if not cache_read_explicit:
        model["cre"] = False
    for key, value in (("ia", ia), ("oa", oa), ("cwa", cwa), ("cra", cra), ("fast", fast)):
        if value is not None:
            model[key] = value
    return model


def compact_from_litellm(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Convert LiteLLM's feed (costs per token) to the compact snapshot schema.

    Entries missing either cost are stubs, so they are skipped. A published cost of
    zero is kept as zero rather than defaulted, which is why `cre` is emitted.
    """
    if not isinstance(raw, dict):
        return {}
    models: Dict[str, Dict[str, Any]] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        input_cost = _number(entry.get("input_cost_per_token"))
        output_cost = _number(entry.get("output_cost_per_token"))
        if input_cost is None or output_cost is None:
            continue
        cache_write = _number(entry.get("cache_creation_input_token_cost"))
        cache_read = _number(entry.get("cache_read_input_token_cost"))
        provider_specific = entry.get("provider_specific_entry")
        fast = _number(provider_specific.get("fast")) if isinstance(provider_specific, dict) else None
        models[key] = _compact_model(
            input_cost * 1e6,
            output_cost * 1e6,
            (input_cost if cache_write is None else cache_write) * 1e6,
            (input_cost * 0.1 if cache_read is None else cache_read) * 1e6,
            cache_read is not None,
            ia=_per_million(entry.get("input_cost_per_token_above_200k_tokens")),
            oa=_per_million(entry.get("output_cost_per_token_above_200k_tokens")),
            cwa=_per_million(entry.get("cache_creation_input_token_cost_above_200k_tokens")),
            cra=_per_million(entry.get("cache_read_input_token_cost_above_200k_tokens")),
            fast=fast,
        )
    return models


def compact_from_models_dev(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Convert models.dev's feed to the compact schema.

    Costs there are already per million. Providers are walked in sorted order and
    the first one to define a model id wins, matching the snapshot generator.
    """
    if not isinstance(raw, dict):
        return {}
    models: Dict[str, Dict[str, Any]] = {}
    for provider_name in sorted(raw):
        provider = raw[provider_name]
        if not isinstance(provider, dict):
            continue
        provider_models = provider.get("models")
        if not isinstance(provider_models, dict):
            continue
        for model_id, model in provider_models.items():
            if model_id in models or not isinstance(model, dict):
                continue
            cost = model.get("cost")
            if not isinstance(cost, dict):
                continue
            input_cost = _number(cost.get("input"))
            output_cost = _number(cost.get("output"))
            if input_cost is None or output_cost is None:
                continue
            cache_write = _number(cost.get("cache_write"))
            cache_read = _number(cost.get("cache_read"))
            models[model_id] = _compact_model(
                input_cost,
                output_cost,
                input_cost if cache_write is None else cache_write,
                input_cost * 0.1 if cache_read is None else cache_read,
                cache_read is not None,
            )
    return models


TRANSFORMS: Dict[str, Callable[[Any], Any]] = {
    "litellm": compact_from_litellm,
    "models.dev": compact_from_models_dev,
    "supplement": lambda raw: raw,
}


def valid_supplement(payload: Any) -> bool:
    """Whether a supplement body has the shape `PricingSupplement.from_dict` reads.

    Checked before caching because the supplement is stored verbatim. A body that
    parses as JSON but carries a null or string rate would be written to disk and
    then raise inside every later `PricingSupplement.from_dict`, which happens on
    every store construction, so the app would fail on each run until someone
    deleted the file by hand.
    """
    if not isinstance(payload, dict):
        return False
    if "updated_at" in payload and not isinstance(payload["updated_at"], str):
        return False

    pricing = payload.get("pricing")
    if pricing is not None:
        if not isinstance(pricing, dict):
            return False
        for entry in pricing.values():
            if not isinstance(entry, dict):
                return False
            for key in (
                "input_per_million",
                "output_per_million",
                "cache_write_per_million",
                "cache_read_per_million",
            ):
                # Presence matters: an explicit null is not the same as an absent key,
                # and `PricingSupplement.from_dict` would call float(None) on it.
                if key in entry and _number(entry.get(key)) is None:
                    return False

    multipliers = payload.get("fast_multipliers")
    if multipliers is not None:
        if not isinstance(multipliers, dict):
            return False
        for value in multipliers.values():
            if _number(value) is None:
                return False

    rules = payload.get("alias_rules")
    if rules is not None:
        if not isinstance(rules, list):
            return False
        for rule in rules:
            if not isinstance(rule, dict):
                return False
            if not isinstance(rule.get("pattern"), str) or not isinstance(rule.get("canonical"), str):
                return False
    return True


def has_supplement_content(payload: Any) -> bool:
    """Whether a supplement body carries anything to apply.

    A feed may legitimately publish alias rules or fast multipliers with no price
    overrides, so requiring a `pricing` block would silently discard it.
    """
    if not isinstance(payload, dict):
        return False
    return any(payload.get(key) for key in ("pricing", "alias_rules", "fast_multipliers"))


class PricingFeeds:
    """Per-source disk cache plus the hourly fetch gate.

    `fetch` is injectable so tests can run offline; it returns the body bytes and
    the ETag, or raises.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        fetch: Optional[Callable[[str, Optional[str]], Tuple[bytes, Optional[str]]]] = None,
        now: Callable[[], float] = time.time,
    ):
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "openusage" / "pricing")
        self._fetch = fetch or self._http_fetch
        self._now = now

    # ── paths ────────────────────────────────────────────────────────

    def cache_file(self, source: str) -> Path:
        return self.cache_dir / f"{source}.json"

    @property
    def state_file(self) -> Path:
        return self.cache_dir / "state.json"

    # ── state ────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def state_for(self, source: str) -> Dict[str, Any]:
        state = self._load_state().get(source)
        return state if isinstance(state, dict) else {}

    def _write_state(self, state: Dict[str, Dict[str, Any]]) -> None:
        try:
            atomic_write_json(self.state_file, state, mode=0o600, indent=2)
        except Exception:
            pass

    def is_due(self, source: str) -> bool:
        state = self.state_for(source)
        now = self._now()
        failed_at = state.get("failed_at")
        if isinstance(failed_at, (int, float)) and now - failed_at < FAILURE_RETRY_SECONDS:
            return False
        attempted_at = state.get("attempted_at")
        fetched_at = state.get("fetched_at")
        if isinstance(attempted_at, (int, float)) and not (
            isinstance(fetched_at, (int, float)) and fetched_at >= attempted_at
        ):
            # The last attempt never recorded an outcome, which means it was killed
            # mid-fetch. Throttle it like a failure so a hang cannot stall every run.
            return now - attempted_at >= FAILURE_RETRY_SECONDS
        if not isinstance(fetched_at, (int, float)):
            return True
        return now - fetched_at >= REFRESH_INTERVAL_SECONDS

    def due_sources(self, force: bool = False) -> List[str]:
        if force:
            return list(SOURCE_ORDER)
        return [source for source in SOURCE_ORDER if self.is_due(source)]

    # ── fetch ────────────────────────────────────────────────────────

    @staticmethod
    def _http_fetch(url: str, etag: Optional[str]) -> Tuple[bytes, Optional[str]]:
        headers = {"User-Agent": "OpenUsage-Linux", "Accept": "application/json"}
        if etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read(MAX_SOURCE_BYTES + 1)
                if len(body) > MAX_SOURCE_BYTES:
                    raise ValueError(f"pricing feed exceeds {MAX_SOURCE_BYTES} bytes")
                return body, response.headers.get("etag")
        except urllib.error.HTTPError as error:
            # urllib raises for any non-2xx, so a 304 (the cached copy is current)
            # arrives here rather than as a response with that status.
            if error.code == 304:
                raise NotModified() from error
            raise

    def refresh(self, force: bool = False) -> Dict[str, bool]:
        """Fetch every due source. Returns {source: changed}. Never raises."""
        state = self._load_state()
        changed: Dict[str, bool] = {}
        wrote_state = False
        started = self._now()
        for source in self.due_sources(force=force):
            url = SOURCE_URLS.get(source)
            if not url:
                continue
            if self._now() - started + TIMEOUT_SECONDS > TOTAL_BUDGET_SECONDS:
                # Not enough budget left for another attempt, so stop rather than
                # overrun the caller. Remaining sources stay due for the next run.
                break
            wrote_state = True
            entry = dict(state.get(source) or {})
            entry["attempted_at"] = self._now()
            self._write_state(state)
            try:
                body, etag = self._fetch(url, entry.get("etag"))
            except NotModified:
                entry["fetched_at"] = self._now()
                entry["failed_at"] = None
                state[source] = entry
                changed[source] = False
                continue
            except Exception:
                entry["failed_at"] = self._now()
                state[source] = entry
                changed[source] = False
                continue

            try:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("pricing feed is not a JSON object")
                if source == "supplement":
                    if not valid_supplement(payload) or not has_supplement_content(payload):
                        raise ValueError("supplement feed has an unreadable shape")
                compact = TRANSFORMS[source](payload)
                if not compact:
                    raise ValueError("pricing feed produced no usable entries")
            except Exception:
                # Validate before writing so a garbage body never replaces good data.
                entry["failed_at"] = self._now()
                state[source] = entry
                changed[source] = False
                continue

            stored = (
                payload
                if source == "supplement"
                else {"retrieved_at": _utc_stamp(), "models": compact}
            )
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self.cache_file(source), stored, mode=0o600)
            except Exception:
                entry["failed_at"] = self._now()
                state[source] = entry
                changed[source] = False
                continue

            entry["etag"] = etag
            entry["fetched_at"] = self._now()
            entry["failed_at"] = None
            state[source] = entry
            changed[source] = True

        if wrote_state:
            # Written once the pass ends, but every branch above also records a
            # failed_at immediately, so a caller that gives up mid-pass still sees
            # the attempt and backs off instead of retrying on the next run.
            self._write_state(state)
        return changed

    # ── load ─────────────────────────────────────────────────────────

    def load_cached(self) -> Dict[str, Any]:
        """Parsed cache documents per source, skipping missing or unreadable files.

        Each value is the whole stored document, so a caller can compare its
        freshness stamp against the bundled copy before trusting it. Catalogs carry
        `models` and `retrieved_at`; the supplement carries its own keys.
        """
        loaded: Dict[str, Any] = {}
        for source in SOURCE_ORDER:
            try:
                with open(self.cache_file(source), "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if source == "supplement":
                if valid_supplement(data) and has_supplement_content(data):
                    loaded[source] = data
                continue
            models = data.get("models")
            if isinstance(models, dict) and models:
                loaded[source] = data
        return loaded


class NotModified(Exception):
    """Raised when the server answers 304, meaning the cached copy is current."""


def _utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
