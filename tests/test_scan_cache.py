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


if __name__ == "__main__":
    unittest.main()
