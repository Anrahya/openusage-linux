"""Main CLI entry point for OpenUsage Linux."""

from __future__ import annotations
import argparse
import sys
import time

from openusage_linux.cli.formatters import render_terminal_card, render_waybar_json
from openusage_linux.core.providers.codex import CodexProvider


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
        from openusage_linux.ui.app import main as run_gui
        sys.exit(run_gui())

    provider = CodexProvider()

    if args.watch:
        try:
            while True:
                # Clear terminal
                sys.stdout.write("\033[2J\033[H")
                snapshot = provider.refresh()
                sys.stdout.write(render_terminal_card(snapshot))
                sys.stdout.flush()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nExiting.")
            sys.exit(0)

    # Single snapshot run
    snapshot = provider.refresh()
    if args.json:
        print(render_waybar_json(snapshot))
    else:
        print(render_terminal_card(snapshot))


if __name__ == "__main__":
    run_cli()
