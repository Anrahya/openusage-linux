"""OpenCode filesystem paths (port of OpenCodePaths)."""

from __future__ import annotations
import os
from typing import List


def _expand_home(value: str) -> str:
    if value == "~" or value.startswith("~/"):
        return os.path.expanduser(value)
    return value


def data_directory() -> str:
    override = os.environ.get("OPENCODE_DATA_DIR", "").strip()
    if override:
        return _expand_home(override).rstrip("/")

    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return os.path.join(_expand_home(xdg).rstrip("/"), "opencode")

    return os.path.join(os.path.expanduser("~"), ".local", "share", "opencode")


def auth_file_path() -> str:
    return os.path.join(data_directory(), "auth.json")


def database_files() -> List[str]:
    """opencode*.db files (stable + preview channels); missing dir -> []."""
    directory = data_directory()
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return []
    except OSError:
        raise
    matched = sorted(
        name for name in names
        if name.startswith("opencode") and name.endswith(".db")
    )
    return [os.path.join(directory, name) for name in matched]
