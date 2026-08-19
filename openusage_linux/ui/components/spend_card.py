"""MacOS-inspired spend cards and compact usage rows for the GTK dashboard."""

from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Callable, List, Optional, Sequence

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from openusage_linux.cli.formatters import format_token_count
from openusage_linux.core.base import DailyUsageSeries, ModelUsageSummary, ProviderSnapshot, ProviderUsageHistory
from openusage_linux.core.settings import METRICS, PERIODS
from openusage_linux.ui.icons import color_for_provider

MIN_SLICE_SHARE = 0.025


def _format_currency(value: float) -> str:
    return f"${value:,.2f}"


def _format_metric(metric: str, tokens: int, cost: float) -> str:
    if metric == "Tokens":
        return format_token_count(tokens)
    if metric == "Cost / MTok":
        if tokens <= 0:
            return "No data"
        return _format_currency(cost / (tokens / 1_000_000))
    return _format_currency(cost)


def _day_entry(history: Optional[ProviderUsageHistory], target: date) -> Optional[DailyUsageSeries]:
    if not history:
        return None
    return history.entry_for_date(target)


def _period_totals(history: Optional[ProviderUsageHistory], period: str) -> tuple[int, float]:
    today = date.today()
    if period == "today":
        entry = _day_entry(history, today)
    elif period == "yesterday":
        entry = _day_entry(history, today - timedelta(days=1))
    else:
        if not history or not history.series:
            return 0, 0.0
        return (
            sum(item.total_tokens for item in history.series),
            sum(item.estimated_cost for item in history.series),
        )
    if not entry:
        return 0, 0.0
    return entry.total_tokens, entry.estimated_cost


def _slice_amount(metric: str, tokens: int, cost: float) -> float:
    if metric == "Tokens":
        return float(tokens)
    if metric == "Cost / MTok":
        return cost / (tokens / 1_000_000) if tokens > 0 else 0.0
    return cost


class SpendRing(Gtk.DrawingArea):
    """A small data ring matching the macOS total-spend card."""

    def __init__(self):
        super().__init__()
        self.set_content_width(104)
        self.set_content_height(104)
        self._slices: List[tuple[str, float]] = []
        self.set_draw_func(self._draw)

    def set_slices(self, slices: Sequence[tuple[str, float]]) -> None:
        self._slices = [(color, max(0.0, share)) for color, share in slices]
        self.queue_draw()

    def set_value(self, has_data: bool, share: float = 1.0) -> None:
        self.set_slices([("#10A37F", 1.0 if has_data else 0.0)])

    @staticmethod
    def _rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
        color = hex_color.lstrip("#")
        return (
            int(color[0:2], 16) / 255.0,
            int(color[2:4], 16) / 255.0,
            int(color[4:6], 16) / 255.0,
            alpha,
        )

    def _draw(self, _area, cr, width: int, height: int, _data=None) -> None:
        cx = width / 2
        cy = height / 2
        line_width = 13
        radius = (min(width, height) - line_width) / 2

        cr.set_line_width(line_width)
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        if not self._slices:
            cr.set_source_rgba(*self._rgba("#8A8A8E", 0.20))
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.stroke()
            return

        gap = 0.75 / radius if len(self._slices) > 1 else 0
        start = -math.pi / 2
        for color, share in self._slices:
            sweep = share * 2 * math.pi
            inset = gap if sweep > 2 * gap else 0
            cr.set_source_rgba(*self._rgba(color))
            cr.arc(cx, cy, radius, start + inset, start + sweep - inset)
            cr.stroke()
            start += sweep


class LegendDot(Gtk.DrawingArea):
    def __init__(self, color: str = "#10A37F"):
        super().__init__()
        self.set_content_width(8)
        self.set_content_height(8)
        self._color = color
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width: int, height: int, _data=None) -> None:
        color = self._color.lstrip("#")
        cr.set_source_rgb(
            int(color[0:2], 16) / 255.0,
            int(color[2:4], 16) / 255.0,
            int(color[4:6], 16) / 255.0,
        )
        cr.arc(width / 2, height / 2, min(width, height) / 2, 0, 2 * math.pi)
        cr.fill()


class TotalSpendCard(Gtk.Box):
    """Period-switchable total spend card using the macOS card anatomy."""

    PERIODS = tuple((key, label) for key, label in zip(PERIODS, ("Today", "Yesterday", "30 Days")))
    METRICS = METRICS

    def __init__(
        self,
        snapshots: Optional[Sequence[ProviderSnapshot]] = None,
        history: Optional[ProviderUsageHistory] = None,
        provider_name: str = "Codex",
        period: str = "today",
        metric: str = "Cost",
        on_period: Optional[Callable[[str], None]] = None,
        on_metric: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("spend-section")
        self.snapshots = list(snapshots or [])
        self.history = history
        self.provider_name = provider_name
        self._period = period if period in PERIODS else "today"
        self._metric = metric if metric in METRICS else "Cost"
        self._on_period = on_period
        self._on_metric = on_metric

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        header.add_css_class("section-header")

        self.metric_button = Gtk.MenuButton()
        self.metric_button.add_css_class("flat")
        self.metric_button.set_tooltip_text("Choose total spend metric")
        self._metric_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._metric_label = Gtk.Label(label=self._metric)
        self._metric_label.add_css_class("section-title")
        self._metric_button_box.append(self._metric_label)
        self._metric_button_box.append(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
        self.metric_button.set_child(self._metric_button_box)
        header.append(self.metric_button)

        names = [snap.provider.display_name for snap in self.snapshots if snap.usage_history]
        info_button = Gtk.Button.new_from_icon_name("dialog-information-symbolic")
        info_button.add_css_class("flat")
        info_button.set_tooltip_text(
            f"Only includes { ' and '.join(names) }." if names else "Local session log estimates for the selected period"
        )
        header.append(info_button)
        header.append(Gtk.Label(hexpand=True))
        self.append(header)

        self._build_metric_menu()

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.card.add_css_class("openusage-card")
        self.card.set_hexpand(True)
        self.append(self.card)

        self._period_buttons: dict[str, Gtk.ToggleButton] = {}
        self._period_picker = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._period_picker.add_css_class("period-switcher")
        for key, label in self.PERIODS:
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("period-segment")
            button.set_hexpand(True)
            button.connect("toggled", self._on_period_toggled, key)
            self._period_picker.append(button)
            self._period_buttons[key] = button
        self.card.append(self._period_picker)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        body.set_margin_start(4)
        body.set_margin_end(4)
        body.set_margin_bottom(2)

        self._ring = SpendRing()
        overlay = Gtk.Overlay()
        overlay.set_child(self._ring)
        self._center_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._center_box.set_halign(Gtk.Align.CENTER)
        self._center_box.set_valign(Gtk.Align.CENTER)
        self._center_value = Gtk.Label()
        self._center_value.add_css_class("spend-total")
        self._center_box.append(self._center_value)
        overlay.add_overlay(self._center_box)
        body.append(overlay)

        self._legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        self._legend.set_valign(Gtk.Align.CENTER)
        body.append(self._legend)
        self.card.append(body)

        self._period_buttons[self._period].set_active(True)
        self._refresh_display()

    def _build_metric_menu(self) -> None:
        popover = Gtk.Popover()
        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        options.set_margin_top(4)
        options.set_margin_bottom(4)
        options.set_margin_start(4)
        options.set_margin_end(4)
        for metric in self.METRICS:
            button = Gtk.Button(label=metric)
            button.add_css_class("flat")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", self._on_metric_clicked, metric, popover)
            options.append(button)
        popover.set_child(options)
        self.metric_button.set_popover(popover)

    def _on_metric_clicked(self, _button, metric: str, popover: Gtk.Popover) -> None:
        self._metric = metric
        self._metric_label.set_label(metric)
        popover.popdown()
        self._refresh_display()
        if self._on_metric:
            self._on_metric(metric)

    def _on_period_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active():
            if self._period == key:
                button.set_active(True)
            return
        self._period = key
        for other_key, other in self._period_buttons.items():
            if other_key != key:
                other.set_active(False)
        self._refresh_display()
        if self._on_period:
            self._on_period(key)

    def _is_dark(self) -> bool:
        try:
            return bool(Adw.StyleManager.get_default().get_dark())
        except Exception:
            return False

    def _provider_rows(self) -> List[tuple[str, str, int, float]]:
        rows: List[tuple[str, str, int, float]] = []
        if self.snapshots:
            for snapshot in self.snapshots:
                tokens, cost = _period_totals(snapshot.usage_history, self._period)
                if tokens <= 0 and cost <= 0:
                    continue
                rows.append((snapshot.provider.id, snapshot.provider.display_name, tokens, cost))
            return rows
        tokens, cost = _period_totals(self.history, self._period)
        if tokens > 0 or cost > 0:
            rows.append(("codex", self.provider_name, tokens, cost))
        return rows

    def _refresh_display(self) -> None:
        rows = self._provider_rows()
        tokens = sum(item[2] for item in rows)
        cost = sum(item[3] for item in rows)
        has_data = tokens > 0 or cost > 0
        period_label = dict(self.PERIODS).get(self._period, "this period")
        self._center_value.set_label(_format_metric(self._metric, tokens, cost) if has_data else "No data")

        while self._legend.get_first_child():
            self._legend.remove(self._legend.get_first_child())
        if not has_data:
            self._ring.set_slices([])
            empty = Gtk.Label(label=f"No data for {period_label.lower()}", xalign=0)
            empty.add_css_class("muted-label")
            self._legend.append(empty)
            return

        amounts = [
            (provider_id, name, _slice_amount(self._metric, item_tokens, item_cost), item_tokens, item_cost)
            for provider_id, name, item_tokens, item_cost in rows
        ]
        amounts = [item for item in amounts if item[2] > 0]
        total = sum(item[2] for item in amounts) or 1.0
        dark = self._is_dark()
        slices = []
        for provider_id, _name, amount, _tokens, _cost in amounts:
            slices.append((color_for_provider(provider_id, dark), max(MIN_SLICE_SHARE, amount / total)))
        share_total = sum(share for _color, share in slices) or 1.0
        self._ring.set_slices([(color, share / share_total) for color, share in slices])

        for provider_id, name, _amount, item_tokens, item_cost in amounts:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            row.append(LegendDot(color_for_provider(provider_id, dark)))
            provider_label = Gtk.Label(label=name, xalign=0)
            provider_label.add_css_class("legend-provider")
            provider_label.set_hexpand(True)
            row.append(provider_label)
            share_label = Gtk.Label(
                label=_format_metric(self._metric, item_tokens, item_cost),
                xalign=1,
            )
            share_label.add_css_class("legend-value")
            row.append(share_label)
            self._legend.append(row)


class SpendRows(Gtk.Box):
    """Compact unbounded rows that sit inside the provider's grouped card."""

    def __init__(self, history: Optional[ProviderUsageHistory]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.history = history
        self.add_css_class("spend-rows")

        today = date.today()
        self._append_day("Today", _day_entry(history, today))
        self._append_day("Yesterday", _day_entry(history, today - timedelta(days=1)))
        total_tokens = sum(item.total_tokens for item in history.series) if history and history.series else 0
        total_cost = sum(item.estimated_cost for item in history.series) if history and history.series else 0.0
        self._append_summary("Last 30 Days", total_tokens > 0 or total_cost > 0, total_tokens, total_cost)

        if history and history.model_usage:
            expander = Gtk.Expander(label=f"Model Breakdown · {len(history.model_usage)} models")
            expander.add_css_class("model-expander")
            models = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            models.set_margin_top(6)
            for model in history.model_usage:
                models.append(self._model_row(model))
            expander.set_child(models)
            self.append(expander)

    def _append_day(self, label: str, entry: Optional[DailyUsageSeries]) -> None:
        has_data = bool(entry and (entry.total_tokens > 0 or entry.estimated_cost > 0))
        self._append_summary(
            label,
            has_data,
            entry.total_tokens if entry else 0,
            entry.estimated_cost if entry else 0.0,
        )

    def _append_summary(self, label: str, has_data: bool, tokens: int, cost: float) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("metric-row")
        title = Gtk.Label(label=label, xalign=0)
        title.set_hexpand(True)
        title.add_css_class("metric-label")
        row.append(title)
        detail = Gtk.Label(
            label=(f"{_format_currency(cost)} · {format_token_count(tokens)} tokens" if has_data else "No data"),
            xalign=1,
        )
        detail.add_css_class("metric-value")
        row.append(detail)
        self.append(row)

    @staticmethod
    def _model_row(model: ModelUsageSummary) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("model-row")
        label = Gtk.Label(label=model.model, xalign=0)
        label.set_hexpand(True)
        label.add_css_class("model-name")
        row.append(label)
        value = Gtk.Label(label=f"{_format_currency(model.estimated_cost)} · {format_token_count(model.total_tokens)}", xalign=1)
        value.add_css_class("muted-label")
        row.append(value)
        return row


class SpendHistoryGroup(Gtk.Box):
    """Compatibility wrapper for callers that want the complete spend section."""

    def __init__(self, history: Optional[ProviderUsageHistory], provider_name: str = "Codex"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.append(TotalSpendCard(history=history, provider_name=provider_name))
        self.append(SpendRows(history))
