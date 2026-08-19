"""User preferences stored at ~/.config/openusage/config.json.

Everything detected is shown by default; users opt out per provider.
Period, spend metric, refresh interval, and the Total Spend toggle persist
across the CLI, GNOME popover, and GTK window.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openusage_linux.core.atomic import atomic_write_json

PERIODS = ("today", "yesterday", "30d")
METRICS = ("Cost", "Cost / MTok", "Tokens")
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 5
REFRESH_CHOICES = (30, 60, 120)

DEFAULTS: Dict[str, Any] = {
    "disabled_providers": [],
    "period": "today",
    "metric": "Cost",
    "refresh_interval": DEFAULT_INTERVAL,
    "show_total_spend": True,
}

_CACHE: Optional[Tuple[str, Optional[float], Dict[str, Any]]] = None


def config_path() -> Path:
    override = os.environ.get("OPENUSAGE_CONFIG", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else Path.home() / ".config"
    return base / "openusage" / "config.json"


def _read_raw(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def normalize_interval(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return max(MIN_INTERVAL, parsed)


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    prefs = dict(raw)
    disabled = raw.get("disabled_providers")
    if not isinstance(disabled, list):
        disabled = []
    prefs["disabled_providers"] = [
        str(item).lower() for item in disabled if isinstance(item, str)
    ]

    period = raw.get("period", DEFAULTS["period"])
    prefs["period"] = period if period in PERIODS else DEFAULTS["period"]

    metric = raw.get("metric", DEFAULTS["metric"])
    prefs["metric"] = metric if metric in METRICS else DEFAULTS["metric"]

    prefs["refresh_interval"] = normalize_interval(
        raw.get("refresh_interval", DEFAULT_INTERVAL)
    )
    prefs["show_total_spend"] = _as_bool(
        raw.get("show_total_spend", DEFAULTS["show_total_spend"]),
        default=True,
    )
    return prefs


def public_prefs(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """UI-facing prefs (no provider hide-list). Missing keys become defaults."""
    src = _normalize(raw or {})
    return {
        "period": src["period"],
        "metric": src["metric"],
        "refresh_interval": src["refresh_interval"],
        "show_total_spend": src["show_total_spend"],
    }


def _load_from_disk() -> Dict[str, Any]:
    path = config_path()
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    global _CACHE
    if _CACHE is not None and _CACHE[0] == key and _CACHE[1] == mtime:
        return dict(_CACHE[2])

    prefs = _normalize(_read_raw(path) if mtime is not None else {})
    _CACHE = (key, mtime, prefs)
    return dict(prefs)


def load_prefs() -> Dict[str, Any]:
    return _load_from_disk()


def _write(prefs: Dict[str, Any]) -> Dict[str, Any]:
    path = config_path()
    normalized = _normalize(prefs)
    atomic_write_json(path, normalized, mode=0o600, indent=2)
    global _CACHE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    _CACHE = (str(path), mtime, dict(normalized))
    return dict(normalized)


def update_prefs(**changes: Any) -> Dict[str, Any]:
    prefs = load_prefs()
    prefs.update(changes)
    return _write(prefs)


def load_disabled() -> List[str]:
    return list(load_prefs().get("disabled_providers") or [])


def is_enabled(provider_id: str) -> bool:
    return provider_id.lower() not in load_disabled()


def set_enabled(provider_id: str, enabled: bool) -> None:
    provider_id = provider_id.lower()
    disabled = set(load_disabled())
    if enabled:
        disabled.discard(provider_id)
    else:
        disabled.add(provider_id)
    update_prefs(disabled_providers=sorted(disabled))
