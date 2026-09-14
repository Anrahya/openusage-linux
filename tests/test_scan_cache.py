"""Tests for the incremental session scan cache."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from openusage_linux.core.scan_cache import ScanCache


class TestScanCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_file = Path(self._tmp.name) / "session_cache.json"
        self.cache = ScanCache(cache_file=self.cache_file)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_and_mtime_miss(self):
        self.cache.set("/tmp/a.jsonl", size=10, mtime=1.0, events=[{"n": 1}])
        self.cache.flush()
        self.assertEqual(os.stat(self.cache_file).st_mode & 0o777, 0o600)

        reloaded = ScanCache(cache_file=self.cache_file)
        self.assertEqual(reloaded.get("/tmp/a.jsonl", 10, 1.0), [{"n": 1}])
        self.assertIsNone(reloaded.get("/tmp/a.jsonl", 10, 2.0))

    def test_prune_keeps_only_listed_paths(self):
        self.cache.set("/old.jsonl", size=1, mtime=1.0, events=[])
        self.cache.set("/keep.jsonl", size=1, mtime=2.0, events=[])
        self.cache.prune(keep_paths=["/keep.jsonl"])
        self.assertNotIn("/old.jsonl", self.cache.entries)
        self.assertIn("/keep.jsonl", self.cache.entries)
        self.cache.flush()
        saved = json.loads(self.cache_file.read_text(encoding="utf-8"))
        self.assertEqual(list(saved), ["/keep.jsonl"])

    def test_prune_missing_reclaims_deleted_files_and_keeps_the_live_one(self):
        live = Path(self._tmp.name) / "live.jsonl"
        live.write_text("x", encoding="utf-8")
        self.cache.set(str(live), size=1, mtime=1.0, events=[{"n": 1}])
        self.cache.set("/gone.jsonl", size=1, mtime=1.0, events=[{"n": 2}])

        self.cache.prune_missing()
        self.assertIn(str(live), self.cache.entries)
        self.assertNotIn("/gone.jsonl", self.cache.entries)

    def test_prune_missing_still_applies_the_size_cap(self):
        # Parsed transcripts are large, and nothing else trims this cache, so an
        # unbounded file would be read and rewritten on every run.
        paths = []
        for index in range(12):
            path = Path(self._tmp.name) / f"m{index}.jsonl"
            path.write_text("x", encoding="utf-8")
            paths.append(str(path))
            self.cache.set(str(path), size=1, mtime=float(index), events=[{"n": index}])

        self.cache.prune_missing(max_entries=5)
        self.assertEqual(len(self.cache.entries), 5)
        # The most recently touched files survive.
        self.assertIn(paths[-1], self.cache.entries)
        self.assertNotIn(paths[0], self.cache.entries)


if __name__ == "__main__":
    unittest.main()
