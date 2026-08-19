"""Atomic JSON writes with optional restrictive permissions."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Union


def atomic_write_json(
    path: Union[str, Path],
    payload: Any,
    mode: Optional[int] = 0o600,
    indent: Optional[int] = None,
) -> None:
    """Write JSON atomically via a sibling tempfile, then replace.

    When ``mode`` is set the tempfile is chmod'd before replace so the
    destination never appears world-readable, even briefly.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=str(target.parent), prefix=f"{target.name}.", suffix=".tmp")
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent)
        os.replace(temp_path, str(target))
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
