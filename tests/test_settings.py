"""Tests for the provider visibility and UI preference store."""

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
        settings._CACHE = None

    def tearDown(self):
        if self._old is None:
            del os.environ["OPENUSAGE_CONFIG"]
        else:
            os.environ["OPENUSAGE_CONFIG"] = self._old
        settings._CACHE = None
        self._tmp.cleanup()

    def test_defaults_to_enabled(self):
        self.assertTrue(settings.is_enabled("claude"))
        self.assertEqual(settings.load_disabled(), [])
        prefs = settings.load_prefs()
        self.assertEqual(prefs["period"], "today")
        self.assertEqual(prefs["metric"], "Cost")
        self.assertEqual(prefs["refresh_interval"], 60)
        self.assertTrue(prefs["show_total_spend"])

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
        with open(os.environ["OPENUSAGE_CONFIG"], "w") as handle:
            handle.write("{broken")
        self.assertTrue(settings.is_enabled("codex"))
        settings.set_enabled("codex", False)
        self.assertFalse(settings.is_enabled("codex"))
        with open(os.environ["OPENUSAGE_CONFIG"]) as handle:
            data = json.load(handle)
        self.assertEqual(data["disabled_providers"], ["codex"])
        self.assertEqual(data["period"], "today")

    def test_saved_config_is_owner_readable_only(self):
        settings.set_enabled("claude", False)
        mode = os.stat(os.environ["OPENUSAGE_CONFIG"]).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_prefs_roundtrip_and_unknown_values(self):
        settings.update_prefs(period="yesterday", metric="Tokens", refresh_interval=30, show_total_spend=False)
        prefs = settings.load_prefs()
        self.assertEqual(prefs["period"], "yesterday")
        self.assertEqual(prefs["metric"], "Tokens")
        self.assertEqual(prefs["refresh_interval"], 30)
        self.assertFalse(prefs["show_total_spend"])

        settings.update_prefs(period="nope", metric="Watts", refresh_interval=1)
        prefs = settings.load_prefs()
        self.assertEqual(prefs["period"], "today")
        self.assertEqual(prefs["metric"], "Cost")
        self.assertEqual(prefs["refresh_interval"], 5)

    def test_toggling_provider_keeps_other_prefs(self):
        settings.update_prefs(period="30d", metric="Cost / MTok", refresh_interval=120)
        settings.set_enabled("codex", False)
        prefs = settings.load_prefs()
        self.assertEqual(prefs["period"], "30d")
        self.assertEqual(prefs["metric"], "Cost / MTok")
        self.assertEqual(prefs["refresh_interval"], 120)
        self.assertEqual(prefs["disabled_providers"], ["codex"])


if __name__ == "__main__":
    unittest.main()
