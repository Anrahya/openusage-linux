"""Grouped provider metric rows matching the macOS dashboard anatomy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from openusage_linux.core.base import MetricFormat, MetricLine, ProviderUsageHistory
from openusage_linux.ui.components.spend_card import SpendRows


def format_countdown_human(resets_at: Optional[datetime]) -> str:
    if not resets_at:
        return ""
    now = datetime.now(timezone.utc)
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)
    total_sec = int((resets_at - now).total_seconds())
    if total_sec <= 0:
        return "Resets now"

    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    minutes = (total_sec % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return f"Resets in {' '.join(parts) if parts else 'less than 1m'}"


def _progress_percent(metric_line: MetricLine) -> float:
    used = metric_line.used or 0.0
    if metric_line.format == MetricFormat.PERCENT:
        return max(0.0, min(100.0, used))
    limit = metric_line.limit or 0.0
    if limit <= 0:
        return 0.0
    return max(0.0, min(100.0, used / limit * 100.0))


def _status_class(used_pct: float) -> str:
    if used_pct >= 90.0:
        return "critical"
    if used_pct >= 80.0:
        return "warning"
    return "normal"


def _value_text(metric_line: MetricLine) -> str:
    parts = []
    for value in metric_line.values:
        if value.kind == MetricFormat.DOLLARS:
            parts.append(f"${value.number:,.2f}")
        elif value.kind == MetricFormat.COUNT:
            suffix = f" {value.label}" if value.label else ""
            parts.append(f"{int(value.number)}{suffix}")
        else:
            parts.append(str(value.number))
    return " · ".join(parts) or "No data"


class MeterRow(Gtk.Box):
    """Label → thin progress meter → used/left and reset context."""

    def __init__(self, metric_line: MetricLine):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("metric-row")
        self.metric_line = metric_line

        used_pct = _progress_percent(metric_line)
        self._show_left = True
        self._used_pct = used_pct
        self._metric_format = metric_line.format

        label_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label=metric_line.label, xalign=0)
        label.set_hexpand(True)
        label.add_css_class("metric-label")
        label_row.append(label)

        self.warning_label = Gtk.Label()
        self.warning_label.add_css_class("metric-warning")
        if used_pct >= 90:
            self.warning_label.set_label("Limit reached")
        elif used_pct >= 80:
            self.warning_label.set_label("Near limit")
        self.warning_label.set_visible(bool(self.warning_label.get_label()))
        label_row.append(self.warning_label)
        self.append(label_row)

        progress = Gtk.ProgressBar()
        progress.set_fraction(used_pct / 100.0)
        progress.set_show_text(False)
        progress.add_css_class("metric-progress")
        progress.add_css_class(_status_class(used_pct))
        self.append(progress)

        context_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.value_button = Gtk.Button()
        self.value_button.add_css_class("metric-primary-button")
        self.value_button.connect("clicked", self._toggle_value)
        context_row.append(self.value_button)
        context_row.append(Gtk.Label(hexpand=True))

        reset_label = Gtk.Label(xalign=1)
        reset_label.add_css_class("metric-context")
        reset_text = format_countdown_human(metric_line.resets_at)
        reset_label.set_label(reset_text)
        if metric_line.resets_at:
            reset_label.set_tooltip_text(metric_line.resets_at.astimezone().strftime("Resets %b %d at %I:%M %p"))
        context_row.append(reset_label)
        self.append(context_row)
        self._update_value_label()

    def _toggle_value(self, _button) -> None:
        self._show_left = not self._show_left
        self._update_value_label()

    def _update_value_label(self) -> None:
        used = self.metric_line.used or 0.0
        limit = self.metric_line.limit or 0.0
        if self._metric_format == MetricFormat.DOLLARS and limit > 0:
            self.value_button.set_label(f"${used:.2f} of ${limit:.2f}")
            self.value_button.set_tooltip_text("Usage in dollars")
            return
        if self._metric_format == MetricFormat.COUNT and limit > 0:
            self.value_button.set_label(f"{int(used)} of {int(limit)}")
            self.value_button.set_tooltip_text("Usage count")
            return
        if self._show_left:
            self.value_button.set_label(f"{max(0.0, 100.0 - self._used_pct):.0f}% left")
            self.value_button.set_tooltip_text("Show percentage used")
        else:
            self.value_button.set_label(f"{self._used_pct:.0f}% used")
            self.value_button.set_tooltip_text("Show percentage left")


class ValueRow(Gtk.Box):
    """Single-line right-aligned value row for unbounded metrics."""

    def __init__(self, metric_line: MetricLine):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("metric-row")
        label = Gtk.Label(label=metric_line.label, xalign=0)
        label.set_hexpand(True)
        label.add_css_class("metric-label")
        self.append(label)
        value = Gtk.Label(label=_value_text(metric_line), xalign=1)
        value.add_css_class("metric-value")
        self.append(value)


class RateLimitsGroup(Gtk.Box):
    """One grouped provider card containing bounded, unbounded, and spend rows."""

    def __init__(self, metric_lines: list[MetricLine], history: Optional[ProviderUsageHistory] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("openusage-card")

        has_rows = False
        for metric_line in metric_lines:
            if metric_line.kind == "progress":
                self.append(MeterRow(metric_line))
                has_rows = True
            elif metric_line.kind == "values":
                self.append(ValueRow(metric_line))
                has_rows = True

        if history is not None:
            self.append(SpendRows(history))
            has_rows = True

        if not has_rows:
            empty = Gtk.Label(label="No data", xalign=0)
            empty.add_css_class("metric-row")
            empty.add_css_class("muted-label")
            self.append(empty)
