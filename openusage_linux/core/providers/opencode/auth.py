"""OpenCode auth store (port of OpenCodeAuthStore)."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from openusage_linux.core.providers.opencode.paths import auth_file_path, database_files


class OpenCodeAuthError(Exception):
    pass


def _key_from_auth_json(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        raise OpenCodeAuthError(
            "Couldn't read OpenCode's auth.json. Check its file permissions or log into OpenCode Go again."
        ) from exc
    entry = data.get("opencode-go") if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    key = entry.get("key")
    if not isinstance(key, str):
        return None
    key = key.strip()
    return key or None


def _key_from_entry(raw: str) -> Optional[str]:
    try:
        data = json.loads(raw)
    except Exception:
        text = raw.strip()
        return text or None
    if isinstance(data, dict):
        key = data.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    return None


def _key_from_databases() -> Optional[str]:
    """Current OpenCode stores the Go key in opencode.db's credential table."""
    try:
        paths = database_files()
    except OSError:
        return None
    for path in paths:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                conn.execute("PRAGMA busy_timeout = 1000")
                rows = conn.execute(
                    "SELECT value FROM credential WHERE integration_id = 'opencode-go'"
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            continue
        for (raw,) in rows:
            if isinstance(raw, str):
                key = _key_from_entry(raw)
                if key:
                    return key
    return None


def go_api_key() -> Optional[str]:
    """The opencode-go key from auth.json or the local OpenCode database."""
    path = auth_file_path()
    if os.path.exists(path):
        key = _key_from_auth_json(path)
        if key:
            return key
    return _key_from_databases()


def has_footprint() -> bool:
    if os.path.exists(auth_file_path()):
        return True
    try:
        return bool(database_files())
    except OSError:
        return True
