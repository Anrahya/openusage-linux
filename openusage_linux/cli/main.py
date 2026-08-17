"""Main CLI entry point for OpenUsage Linux."""

from __future__ import annotations
import argparse
import sys
import time
from typing import List

from openusage_linux.cli.formatters import render_terminal_card, render_waybar_json
from openusage_linux.core.base import ProviderSnapshot
from openusage_linux.core.providers import ProviderCatalog

NO_PROVIDERS_MESSAGE = """\
◆ OPENUSAGE — no providers detected
────────────────────────────────────────────────────────────────
  Log in with any supported tool and OpenUsage picks it up:

  • Codex CLI      codex login            (~/.codex/auth.json)
  • Claude Code    claude                 (~/.claude/.credentials.json)
  • Cursor         sign in via Cursor app (~/.config/Cursor/...)
  • OpenCode       log in with OpenCode   (~/.local/share/opencode)
────────────────────────────────────────────────────────────────
"""


def collect_snapshots() -> List[ProviderSnapshot]:
    """Refresh every provider that has local credentials."""
    snapshots: List[ProviderSnapshot] = []
    for provider in ProviderCatalog.get_all_providers():
        try:
            if not provider.has_local_credentials():
                continue
        except Exception:
            continue
        try:
            snapshots.append(provider.refresh())
        except Exception as e:
            snapshots.append(
                ProviderSnapshot.error_snapshot(provider.provider, f"Unexpected error: {e}")
            )
    return snapshots


def run_cli():
    parser = argparse.ArgumentParser(
        prog="openusage-linux",
        description="OpenUsage for Linux: Track AI subscription quotas, rate limits, and token usage.",
    )
    parser.add_argument(
        "--gui", action="store_true", help="Launch native GNOME GTK4 / Libadwaita desktop application"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON formatted for Waybar / Polybar status bars"
    )
    parser.add_argument(
        "--watch", "-w", action="store_true", help="Continuously watch and update terminal dashboard"
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=60, help="Refresh interval in seconds (default: 60)"
    )

    args = parser.parse_args()

    if args.gui:
        try:
            from openusage_linux.ui.app import main as run_gui
        except ImportError:
            print(
                "The desktop window needs GTK4/Libadwaita (PyGObject).\n"
                "  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita\n"
                "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
                "The CLI and top-bar extension work without it: try `openusage-linux`."
            )
            sys.exit(1)
        sys.exit(run_gui())

    def render_once() -> str:
        snapshots = collect_snapshots()
        if not snapshots:
            return NO_PROVIDERS_MESSAGE
        if args.json:
            return render_waybar_json(snapshots)
        return render_terminal_card(snapshots)

    if args.watch:
        try:
            while True:
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.write(render_once())
                sys.stdout.flush()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nExiting.")
            sys.exit(0)

    print(render_once())


if __name__ == "__main__":
    run_cli()
