"""GTK4 Meter Card for rate limits and quotas."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from openusage_linux.core.base import MetricLine


def format_countdown_human(resets_at: Optional[datetime]) -> str:
    if not resets_at:
        return ""
    now = datetime.now(timezone.utc)
    diff = resets_at - now
    total_sec = int(diff.total_seconds())
    if total_sec <= 0:
        return "Resets now"

    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    minutes = (total_sec % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes}m")

    time_str = " ".join(parts) if parts else "less than 1m"
    local_dt = resets_at.astimezone()
    date_str = local_dt.strftime("%b %d, %I:%M %p")
    return f"Resets in {time_str} ({date_str})"


class MeterRow(Gtk.Box):
    def __init__(self, metric_line: MetricLine):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_margin_start(12)
        self.set_margin_end(12)

        used_pct = metric_line.used if metric_line.used is not None else 0.0
        clamped_pct = max(0.0, min(100.0, used_pct))

        # Top label row: Title on left, percentage on right
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        
        lbl_title = Gtk.Label(label=metric_line.label, xalign=0)
        lbl_title.set_hexpand(True)
        lbl_title.add_css_class("heading")

        status_class = "normal"
        if clamped_pct >= 90.0:
            status_class = "critical"
        elif clamped_pct >= 75.0:
            status_class = "warning"

        lbl_pct = Gtk.Label(label=f"{clamped_pct:.1f}%")
        lbl_pct.add_css_class("meter-percentage")
        lbl_pct.add_css_class(status_class)

        top_box.append(lbl_title)
        top_box.append(lbl_pct)
        self.append(top_box)

        # Progress bar
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_fraction(clamped_pct / 100.0)
        progress_bar.add_css_class("progress-meter")
        progress_bar.add_css_class(status_class)
        self.append(progress_bar)

        # Subtitle countdown
        cd_text = format_countdown_human(metric_line.resets_at)
        if cd_text:
            lbl_cd = Gtk.Label(label=cd_text, xalign=0)
            lbl_cd.add_css_class("countdown-label")
            self.append(lbl_cd)


class RateLimitsGroup(Adw.PreferencesGroup):
    def __init__(self, metric_lines: list[MetricLine]):
        super().__init__()
        self.set_title("Quotas &amp; Rate Limits")
        self.set_description("Live usage reported by Codex backend")

        card_box = Gtk.ListBox()
        card_box.set_selection_mode(Gtk.SelectionMode.NONE)
        card_box.add_css_class("boxed-list")

        has_any = False
        for ml in metric_lines:
            if ml.kind == "progress":
                row = MeterRow(ml)
                card_box.append(row)
                has_any = True

        if has_any:
            self.add(card_box)
        else:
            no_data_row = Adw.ActionRow(title="No active rate limits reported")
            self.add(no_data_row)
