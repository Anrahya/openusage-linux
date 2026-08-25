"""The EGO zip must ship the CLI so the top-bar icon works without pip."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACK_SH = os.path.join(REPO_ROOT, "gnome-extension", "pack.sh")


class TestExtensionPack(unittest.TestCase):
    def test_pack_bundles_cli_without_gtk(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "openusage.shell-extension.zip")
            subprocess.run(
                ["bash", PACK_SH, out],
                check=True,
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
            )
            extract = os.path.join(tmp, "ext")
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                archive.extractall(extract)
            env = os.environ.copy()
            env["PYTHONPATH"] = os.path.join(extract, "python")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [sys.executable, "-m", "openusage_linux", "--help"],
                cwd=extract,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn("metadata.json", names)
        self.assertIn("extension.js", names)
        self.assertIn("stylesheet.css", names)
        self.assertIn("openusage.svg", names)
        self.assertIn("python/openusage_linux/__main__.py", names)
        self.assertIn("python/openusage_linux/cli/main.py", names)
        self.assertTrue(any(name.endswith("pricing_supplement.json") for name in names))
        self.assertFalse(any("/ui/" in name for name in names))
        self.assertFalse(any(name.endswith(".pyc") for name in names))
        self.assertIn("--json", result.stdout)


class TestModuleEntrypoint(unittest.TestCase):
    def test_repo_module_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "openusage_linux", "--help"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--json", result.stdout)


if __name__ == "__main__":
    unittest.main()
