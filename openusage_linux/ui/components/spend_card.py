"""GTK4 Spend Card for token analytics and model breakdown."""

from __future__ import annotations
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from openusage_linux.cli.formatters import format_token_count
from openusage_linux.core.base import ProviderUsageHistory


class SpendHistoryGroup(Adw.PreferencesGroup):
    def __init__(self, history: Optional[ProviderUsageHistory]):
        super().__init__()
        self.set_title("Token Usage & Estimated Spend")
        self.set_description("Calculated locally from ~/.codex session logs")

        if not history or not history.series:
            row = Adw.ActionRow(title="No local session logs found")
            self.add(row)
            return

        # Stat Tiles Box (Today vs 7-day total)
        today_entry = history.series[-1]
        total_tokens_30d = sum(s.total_tokens for s in history.series)
        total_cost_30d = sum(s.estimated_cost for s in history.series)

        tiles_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tiles_box.set_homogeneous(True)
        tiles_box.set_margin_bottom(12)

        # Tile 1: Today
        tile_today = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile_today.add_css_class("card")
        tile_today.set_margin_top(4)
        tile_today.set_margin_bottom(4)
        tile_today.set_margin_start(4)
        tile_today.set_margin_end(4)

        lbl_today_val = Gtk.Label(label=f"${today_entry.estimated_cost:.2f}")
        lbl_today_val.add_css_class("stat-value")
        lbl_today_sub = Gtk.Label(label=f"Today ({format_token_count(today_entry.total_tokens)} tokens)")
        lbl_today_sub.add_css_class("stat-label")
        tile_today.append(lbl_today_val)
        tile_today.append(lbl_today_sub)
        tiles_box.append(tile_today)

        # Tile 2: 30 Days Total
        tile_30d = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile_30d.add_css_class("card")
        tile_30d.set_margin_top(4)
        tile_30d.set_margin_bottom(4)
        tile_30d.set_margin_start(4)
        tile_30d.set_margin_end(4)

        lbl_30d_val = Gtk.Label(label=f"${total_cost_30d:.2f}")
        lbl_30d_val.add_css_class("stat-value")
        lbl_30d_sub = Gtk.Label(label=f"30 Days ({format_token_count(total_tokens_30d)} tokens)")
        lbl_30d_sub.add_css_class("stat-label")
        tile_30d.append(lbl_30d_val)
        tile_30d.append(lbl_30d_sub)
        tiles_box.append(tile_30d)

        self.add(tiles_box)

        # Model Breakdown Expander Row
        if history.model_usage:
            expander = Adw.ExpanderRow()
            expander.set_title("Model Breakdown")
            expander.set_subtitle(f"{len(history.model_usage)} models used")
            expander.set_icon_name("utilities-system-monitor-symbolic")

            for m in history.model_usage:
                m_row = Adw.ActionRow()
                m_row.set_title(m.model)
                m_row.set_subtitle(
                    f"{format_token_count(m.total_tokens)} tokens (in: {format_token_count(m.input_tokens)}, cached: {format_token_count(m.cached_tokens)}, out: {format_token_count(m.output_tokens)})"
                )
                
                cost_lbl = Gtk.Label(label=f"${m.estimated_cost:.2f}")
                cost_lbl.add_css_class("model-row-cost")
                m_row.add_suffix(cost_lbl)
                expander.add_row(m_row)

            self.add(expander)
