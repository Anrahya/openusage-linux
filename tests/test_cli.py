"""CLI argument and catalog tests that do not hit the network."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from openusage_linux.cli import main
from openusage_linux.core import settings
from openusage_linux.core.providers import ProviderCatalog


class TestCli(unittest.TestCase):
    def test_unknown_provider_lists_public_ids(self):
        with patch("sys.argv", ["openusage-linux", "--enable", "nope"]):
            with self.assertRaises(SystemExit) as raised:
                with redirect_stdout(io.StringIO()) as stdout:
                    main.run_cli()
        self.assertEqual(raised.exception.code, 2)
        output = stdout.getvalue()
        self.assertIn("codex", output)
        self.assertIn("claude", output)

    def test_interval_below_minimum_is_rejected(self):
        with patch("sys.argv", ["openusage-linux", "--interval", "0"]):
            with self.assertRaises(SystemExit):
                main.run_cli()

    def test_json_empty_state_is_still_json(self):
        with patch("sys.argv", ["openusage-linux", "--json"]):
            with patch("openusage_linux.cli.main.collect_snapshots", return_value=[]):
                with patch("openusage_linux.cli.main.available_providers", return_value=[]):
                    with redirect_stdout(io.StringIO()) as stdout:
                        main.run_cli()
        payload = __import__("json").loads(stdout.getvalue())
        self.assertTrue(payload["is_error"])
        self.assertEqual(payload["error"], "No providers detected")

    def test_set_pref_rejects_unknown_key(self):
        with patch("sys.argv", ["openusage-linux", "--set-pref", "density=compact"]):
            with self.assertRaises(SystemExit):
                main.run_cli()

    def test_set_pref_persists_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with patch.dict(os.environ, {"OPENUSAGE_CONFIG": path}, clear=False):
                settings._CACHE = None
                with patch(
                    "sys.argv",
                    ["openusage-linux", "--set-pref", "period=yesterday", "--set-pref", "refresh_interval=30"],
                ):
                    with redirect_stdout(io.StringIO()) as stdout:
                        main.run_cli()
                settings._CACHE = None
                prefs = settings.load_prefs()
        self.assertIn("period=yesterday", stdout.getvalue())
        self.assertEqual(prefs["period"], "yesterday")
        self.assertEqual(prefs["refresh_interval"], 30)

    def test_catalog_known_ids(self):
        ids = {provider.provider.id for provider in ProviderCatalog.get_all_providers()}
        self.assertEqual(ids, {"codex", "claude", "cursor", "opencode", "grok"})
        self.assertIsNone(ProviderCatalog.get_provider("nope"))


if __name__ == "__main__":
    unittest.main()
