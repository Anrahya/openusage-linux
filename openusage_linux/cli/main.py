"""Main CLI entry point for OpenUsage Linux."""

from __future__ import annotations
import argparse
import sys
import time
from typing import List

from openusage_linux.cli.formatters import render_terminal_card, render_waybar_json
from openusage_linux.core.base import ProviderSnapshot
from openusage_linux.core.providers import ProviderCatalog
from openusage_linux.core.settings import is_enabled, set_enabled

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

NO_VISIBLE_PROVIDERS_MESSAGE = """\
◆ OPENUSAGE — all detected providers are hidden
────────────────────────────────────────────────────────────────
  Show one again with:   openusage-linux --enable <provider>
  List what's detected:  openusage-linux --list
────────────────────────────────────────────────────────────────
"""


def collect_snapshots() -> List[ProviderSnapshot]:
    """Refresh every provider that has local credentials and is enabled."""
    snapshots: List[ProviderSnapshot] = []
    for provider in ProviderCatalog.get_all_providers():
        provider_id = provider.provider.id
        if not is_enabled(provider_id):
            continue
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


def enabled_providers() -> List[str]:
    """Ids of providers that are enabled AND have local credentials."""
    found: List[str] = []
    for provider in ProviderCatalog.get_all_providers():
        if not is_enabled(provider.provider.id):
            continue
        try:
            if provider.has_local_credentials():
                found.append(provider.provider.id)
        except Exception:
            continue
    return found


def available_providers() -> List[dict]:
    """Every detected provider with its enabled state (for UI toggles)."""
    result: List[dict] = []
    for provider in ProviderCatalog.get_all_providers():
        if not _safe_has_credentials(provider):
            continue
        result.append({
            "id": provider.provider.id,
            "display_name": provider.provider.display_name,
            "enabled": is_enabled(provider.provider.id),
        })
    return result


def print_provider_list() -> None:
    print(f"\n{'PROVIDER':<12} {'STATUS':<12} NOTES")
    print("─" * 60)
    for provider in ProviderCatalog.get_all_providers():
        provider_id = provider.provider.id
        try:
            has_creds = provider.has_local_credentials()
        except Exception:
            has_creds = False
        shown = is_enabled(provider_id) and has_creds
        if shown:
            status = "shown"
        elif has_creds:
            status = "hidden"
        else:
            status = "not detected"
        notes = "" if shown else (
            f"show with: openusage-linux --enable {provider_id}" if has_creds
            else "log in with its CLI to detect"
        )
        print(f"{provider_id:<12} {status:<12} {notes}")
    print()


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
    parser.add_argument(
        "--list", action="store_true", help="List providers, whether they are detected, and shown/hidden"
    )
    parser.add_argument(
        "--enable", metavar="PROVIDER", help="Show this provider (e.g. --enable claude)"
    )
    parser.add_argument(
        "--disable", metavar="PROVIDER", help="Hide this provider (e.g. --disable cursor)"
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

    if args.enable or args.disable:
        target = args.enable or args.disable
        provider = ProviderCatalog.get_provider(target)
        if provider is None:
            known = ", ".join(sorted(ProviderCatalog._registry.keys()))
            print(f"Unknown provider '{target}'. Known providers: {known}")
            sys.exit(2)
        set_enabled(provider.provider.id, enabled=bool(args.enable))
        verb = "shown" if args.enable else "hidden"
        print(f"{provider.provider.display_name} is now {verb}.")
        return

    if args.list:
        print_provider_list()
        return

    def render_once() -> str:
        snapshots = collect_snapshots()
        if not snapshots:
            any_detected = any(
                _safe_has_credentials(p) for p in ProviderCatalog.get_all_providers()
            )
            return NO_VISIBLE_PROVIDERS_MESSAGE if any_detected else NO_PROVIDERS_MESSAGE
        if args.json:
            return render_waybar_json(snapshots, available_providers())
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


def _safe_has_credentials(provider) -> bool:
    try:
        return provider.has_local_credentials()
    except Exception:
        return False


if __name__ == "__main__":
    run_cli()
