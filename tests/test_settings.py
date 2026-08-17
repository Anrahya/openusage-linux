"""Tests for the provider visibility config store."""

import json
import os
import tempfile
import unittest

from openusage_linux.core import settings


class TestProviderConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("OPENUSAGE_CONFIG")
        os.environ["OPENUSAGE_CONFIG"] = os.path.join(self._tmp.name, "config.json")

    def tearDown(self):
        if self._old is None:
            del os.environ["OPENUSAGE_CONFIG"]
        else:
            os.environ["OPENUSAGE_CONFIG"] = self._old
        self._tmp.cleanup()

    def test_defaults_to_enabled(self):
        self.assertTrue(settings.is_enabled("claude"))
        self.assertEqual(settings.load_disabled(), [])

    def test_disable_and_enable_roundtrip(self):
        settings.set_enabled("claude", False)
        self.assertFalse(settings.is_enabled("claude"))
        self.assertTrue(settings.is_enabled("codex"))

        settings.set_enabled("cursor", False)
        self.assertEqual(settings.load_disabled(), ["claude", "cursor"])

        settings.set_enabled("claude", True)
        self.assertEqual(settings.load_disabled(), ["cursor"])
        self.assertTrue(settings.is_enabled("claude"))

    def test_case_insensitive_ids(self):
        settings.set_enabled("Claude", False)
        self.assertFalse(settings.is_enabled("claude"))
        settings.set_enabled("CLAUDE", True)
        self.assertTrue(settings.is_enabled("claude"))

    def test_corrupt_config_treated_as_empty(self):
        with open(os.environ["OPENUSAGE_CONFIG"], "w") as f:
            f.write("{broken")
        self.assertTrue(settings.is_enabled("codex"))
        # Saving over a corrupt file still works.
        settings.set_enabled("codex", False)
        self.assertFalse(settings.is_enabled("codex"))
        with open(os.environ["OPENUSAGE_CONFIG"]) as f:
            self.assertEqual(json.load(f), {"disabled_providers": ["codex"]})


if __name__ == "__main__":
    unittest.main()
