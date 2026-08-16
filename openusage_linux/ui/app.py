"""Adw.Application lifecycle and CSS loader."""

from __future__ import annotations
import sys
from pathlib import Path
from typing import List, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk

from openusage_linux.ui.window import OpenUsageWindow


class OpenUsageApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.openusage.OpenUsage",
            flags=0,
        )
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._load_css()

    def do_activate(self):
        if not self.window:
            self.window = OpenUsageWindow(self)
        self.window.present()

    def _load_css(self):
        css_path = Path(__file__).parent / "style.css"
        if css_path.exists():
            css_provider = Gtk.CssProvider()
            try:
                css_provider.load_from_path(str(css_path))
                display = Gdk.Display.get_default()
                if display:
                    Gtk.StyleContext.add_provider_for_display(
                        display,
                        css_provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
            except Exception as e:
                print(f"Warning: Failed to load style.css: {e}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    app = OpenUsageApplication()
    # Pass clean argv containing only the executable name so Gio.Application doesn't reject custom CLI flags
    clean_argv = [sys.argv[0]] if argv is None else argv
    return app.run(clean_argv)


if __name__ == "__main__":
    sys.exit(main())
