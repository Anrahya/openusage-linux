"""Compatibility wrapper for unbounded provider credit rows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from openusage_linux.core.base import MetricLine
from openusage_linux.ui.components.meter_card import ValueRow


class CreditsGroup(Gtk.Box):
    """Render credit/reset values using the same grouped-row treatment as the dashboard."""

    def __init__(self, metric_lines: list[MetricLine]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("openusage-card")
        rows = [line for line in metric_lines if line.kind in ("values", "badge")]
        for line in rows:
            self.append(ValueRow(line))
        self.set_visible(bool(rows))
