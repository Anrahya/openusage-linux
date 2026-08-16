"""GTK4 Credits & Rate Limit Resets Card."""

from __future__ import annotations
from typing import List

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from openusage_linux.core.base import MetricFormat, MetricLine


class CreditsGroup(Adw.PreferencesGroup):
    def __init__(self, metric_lines: List[MetricLine]):
        super().__init__()
        self.set_title("Resets &amp; Extra Usage")

        has_any = False
        for ml in metric_lines:
            if ml.kind == "values":
                has_any = True
                row = Adw.ActionRow()
                row.set_title(ml.label)
                
                # Format value string
                val_parts = []
                for v in ml.values:
                    if v.kind == MetricFormat.DOLLARS:
                        val_parts.append(f"${v.number:.2f}")
                    elif v.kind == MetricFormat.COUNT:
                        lbl = f" {v.label}" if v.label else ""
                        val_parts.append(f"{int(v.number)}{lbl}")

                row.set_subtitle(" · ".join(val_parts))

                if ml.label == "Rate Limit Resets":
                    row.set_icon_name("view-refresh-symbolic")
                else:
                    row.set_icon_name("folder-saved-search-symbolic")

                self.add(row)

        if not has_any:
            self.set_visible(False)
