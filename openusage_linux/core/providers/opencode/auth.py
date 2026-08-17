"""OpenCode auth store (port of OpenCodeAuthStore)."""

from __future__ import annotations
import json
from typing import Optional

from openusage_linux.core.providers.opencode.paths import auth_file_path


class OpenCodeAuthError(Exception):
    pass


def go_api_key() -> Optional[str]:
    """The opencode-go key from auth.json. Raises if the file exists but is broken."""
    import os
    path = auth_file_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise OpenCodeAuthError(
            "Couldn't read OpenCode's auth.json. Check its file permissions or log into OpenCode Go again."
        ) from e
    entry = data.get("opencode-go") if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    key = entry.get("key")
    if not isinstance(key, str):
        return None
    key = key.strip()
    return key or None


def has_footprint() -> bool:
    import os
    return os.path.exists(auth_file_path())
