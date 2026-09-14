"""Persistent incremental JSONL session scan cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from openusage_linux.core.atomic import atomic_write_json


class ScanCache:
    _instance: Optional[ScanCache] = None

    def __init__(self, cache_file: Optional[Path] = None):
        self.cache_file = cache_file or (Path.home() / ".cache" / "openusage" / "session_cache.json")
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.entries: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load()

    @classmethod
    def get_shared(cls) -> ScanCache:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.entries = data
            except Exception:
                self.entries = {}

    def get(self, path: str, size: int, mtime: float) -> Optional[List[dict]]:
        entry = self.entries.get(path)
        if not entry:
            return None
        if entry.get("size") == size and abs(entry.get("mtime", 0.0) - mtime) < 0.001:
            return entry.get("events")
        return None

    def set(self, path: str, size: int, mtime: float, events: List[dict]):
        self.entries[path] = {
            "size": size,
            "mtime": mtime,
            "events": events,
        }
        self._dirty = True

    def prune(self, keep_paths: Optional[List[str]] = None, max_entries: int = 400) -> None:
        """Drop stale file entries so the cache cannot grow without bound."""
        if keep_paths is not None:
            keep = set(keep_paths)
            stale = [path for path in self.entries if path not in keep]
            for path in stale:
                del self.entries[path]
                self._dirty = True
        if len(self.entries) > max_entries:
            # Prefer recently-touched files; fall back to insertion order.
            ranked = sorted(
                self.entries.items(),
                key=lambda item: float(item[1].get("mtime") or 0.0),
                reverse=True,
            )
            self.entries = dict(ranked[:max_entries])
            self._dirty = True

    def prune_missing(self, max_entries: int = 400) -> None:
        """Drop entries whose file no longer exists, then trim to `max_entries`.

        Scanners share one cache, so ``prune(keep_paths=...)`` is only safe for a
        scanner that owns every entry: it deletes anything not in its own list, which
        would evict a sibling scanner's work on every refresh. Pruning by existence
        instead reclaims deleted sessions without touching another provider's files,
        and the size cap still applies because nothing else enforces it. Parsed
        transcripts are large, so an untrimmed cache is read and rewritten on every
        run.
        """
        for path in list(self.entries):
            if not os.path.exists(path):
                del self.entries[path]
                self._dirty = True
        if len(self.entries) > max_entries:
            # Prefer recently-touched files; fall back to insertion order.
            ranked = sorted(
                self.entries.items(),
                key=lambda item: float(item[1].get("mtime") or 0.0),
                reverse=True,
            )
            self.entries = dict(ranked[:max_entries])
            self._dirty = True

    def flush(self):
        if not self._dirty:
            return
        try:
            atomic_write_json(self.cache_file, self.entries, mode=0o600)
            self._dirty = False
        except Exception:
            pass
