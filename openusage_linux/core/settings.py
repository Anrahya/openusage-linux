"""User provider preferences — which detected providers to show or hide.

Everything detected is shown by default; users opt out per provider.
Stored at ~/.config/openusage/config.json as {"disabled_providers": [...]}.
"""

from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import List


def config_path() -> Path:
    override = os.environ.get("OPENUSAGE_CONFIG", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else Path.home() / ".config"
    return base / "openusage" / "config.json"


def load_disabled() -> List[str]:
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        disabled = data.get("disabled_providers")
        if isinstance(disabled, list):
            return [str(p).lower() for p in disabled if isinstance(p, str)]
    except Exception:
        pass
    return []


def _save(disabled: List[str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"disabled_providers": sorted(set(disabled))}
    fd, temp_path = tempfile.mkstemp(dir=str(path.parent), prefix="config_", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(temp_path, str(path))
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def is_enabled(provider_id: str) -> bool:
    return provider_id.lower() not in load_disabled()


def set_enabled(provider_id: str, enabled: bool) -> None:
    provider_id = provider_id.lower()
    disabled = set(load_disabled())
    if enabled:
        disabled.discard(provider_id)
    else:
        disabled.add(provider_id)
    _save(list(disabled))
