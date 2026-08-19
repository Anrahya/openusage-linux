"""Main Libadwaita application window, shaped after the macOS OpenUsage popover."""

from __future__ import annotations

import threading
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from openusage_linux.cli.main import collect_snapshots
from openusage_linux.core.base import ProviderSnapshot
from openusage_linux.core.settings import REFRESH_CHOICES, load_prefs, update_prefs
from openusage_linux.ui.components.meter_card import RateLimitsGroup
from openusage_linux.ui.components.spend_card import TotalSpendCard
from openusage_linux.ui.icons import provider_icon_path
from openusage_linux.version import __version__


class OpenUsageWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="OpenUsage")
        self.set_default_size(420, 620)
        self.set_size_request(360, 520)

        prefs = load_prefs()
        self.current_snapshots: List[ProviderSnapshot] = []
        self._is_refreshing = False
        self._refresh_interval = int(prefs["refresh_interval"])
        self._seconds_until_refresh = self._refresh_interval
        self._period = prefs["period"]
        self._metric = prefs["metric"]
        self._show_total_spend = bool(prefs["show_total_spend"])
        self._last_refresh_error: Optional[str] = None

        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

        self.header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(self.header_bar)
        title = Gtk.Label(label="OpenUsage")
        title.add_css_class("app-title")
        self.header_bar.set_title_widget(title)

        self.btn_refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.btn_refresh.set_tooltip_text("Refresh usage metrics")
        self.btn_refresh.connect("clicked", self._on_refresh_clicked)
        self.header_bar.pack_end(self.btn_refresh)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.toolbar_view.set_content(self.scrolled_window)

        self.clamp = Adw.Clamp()
        self.clamp.set_maximum_size(520)
        self.clamp.set_tightening_threshold(400)
        self.clamp.add_css_class("dashboard-clamp")
        self.scrolled_window.set_child(self.clamp)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.content_box.add_css_class("dashboard-content")
        self.clamp.set_child(self.content_box)
        self._show_loading()

        self._build_footer()
        self.trigger_refresh()
        GLib.timeout_add_seconds(1, self._on_second)

    def _build_footer(self) -> None:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("app-footer")

        version = Gtk.Label(label=f"OpenUsage {__version__}", xalign=0)
        version.add_css_class("footer-label")
        footer.append(version)

        self.lbl_next_update = Gtk.Label(label=self._format_next_update(), xalign=0)
        self.lbl_next_update.add_css_class("footer-label")
        self.lbl_next_update.set_hexpand(True)
        footer.append(self.lbl_next_update)

        options_button = Gtk.MenuButton(label="Options")
        options_button.add_css_class("flat")
        options_button.add_css_class("footer-action")
        popover = Gtk.Popover()
        option_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        option_box.set_margin_top(4)
        option_box.set_margin_bottom(4)
        option_box.set_margin_start(4)
        option_box.set_margin_end(4)

        refresh_option = Gtk.Button(label="Refresh now")
        refresh_option.add_css_class("flat")
        refresh_option.connect("clicked", self._on_refresh_option, popover)
        option_box.append(refresh_option)

        self._spend_check = Gtk.CheckButton(label="Show Total Spend")
        self._spend_check.set_active(self._show_total_spend)
        self._spend_check.connect("toggled", self._on_spend_toggled)
        option_box.append(self._spend_check)

        for seconds in REFRESH_CHOICES:
            button = Gtk.Button(label=f"Refresh every {seconds}s")
            button.add_css_class("flat")
            button.connect("clicked", self._on_interval_clicked, seconds, popover)
            option_box.append(button)

        popover.set_child(option_box)
        options_button.set_popover(popover)
        footer.append(options_button)
        self.toolbar_view.add_bottom_bar(footer)

    def _on_refresh_option(self, _button, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.trigger_refresh()

    def _on_refresh_clicked(self, _button) -> None:
        self.trigger_refresh()

    def _on_spend_toggled(self, button: Gtk.CheckButton) -> None:
        self._show_total_spend = button.get_active()
        update_prefs(show_total_spend=self._show_total_spend)
        self._render_snapshots(self.current_snapshots)

    def _on_interval_clicked(self, _button, seconds: int, popover: Gtk.Popover) -> None:
        popover.popdown()
        self._refresh_interval = seconds
        self._seconds_until_refresh = seconds
        update_prefs(refresh_interval=seconds)
        self.lbl_next_update.set_label(self._format_next_update())

    def _on_period_changed(self, period: str) -> None:
        self._period = period
        update_prefs(period=period)

    def _on_metric_changed(self, metric: str) -> None:
        self._metric = metric
        update_prefs(metric=metric)

    def _format_next_update(self) -> str:
        remaining = max(0, self._seconds_until_refresh)
        if remaining >= 60:
            return f"Next update in {math_ceil_minutes(remaining)}m"
        return f"Next update in {remaining}s"

    def _on_second(self) -> bool:
        if not self._is_refreshing:
            self._seconds_until_refresh -= 1
        if self._seconds_until_refresh <= 0:
            self.trigger_refresh()
        self.lbl_next_update.set_label(self._format_next_update())
        return True

    def trigger_refresh(self) -> None:
        if self._is_refreshing:
            return
        self._is_refreshing = True
        self._seconds_until_refresh = self._refresh_interval
        self.btn_refresh.set_sensitive(False)
        self.btn_refresh.set_icon_name("process-working-symbolic")
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            snapshots = collect_snapshots()
            error = None
        except Exception as exc:
            snapshots = []
            error = str(exc)
        GLib.idle_add(self._apply_snapshots, snapshots, error)

    def _clear_content(self) -> None:
        while self.content_box.get_first_child():
            self.content_box.remove(self.content_box.get_first_child())

    def _show_loading(self) -> None:
        self._clear_content()
        state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        state.add_css_class("status-state")
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_halign(Gtk.Align.CENTER)
        state.append(spinner)
        title = Gtk.Label(label="Loading usage")
        title.add_css_class("status-title")
        state.append(title)
        detail = Gtk.Label(label="Fetching live quotas and local session totals", wrap=True)
        detail.add_css_class("muted-label")
        detail.set_justify(Gtk.Justification.CENTER)
        state.append(detail)
        self.content_box.append(state)

    def _status_state(self, title_text: str, detail_text: str, icon_name: str) -> Gtk.Box:
        state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        state.add_css_class("status-state")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(28)
        icon.set_halign(Gtk.Align.CENTER)
        state.append(icon)
        title = Gtk.Label(label=title_text)
        title.add_css_class("status-title")
        state.append(title)
        detail = Gtk.Label(label=detail_text, wrap=True)
        detail.add_css_class("muted-label")
        detail.set_justify(Gtk.Justification.CENTER)
        state.append(detail)
        return state

    def _provider_header(self, snapshot: ProviderSnapshot) -> Gtk.Box:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        header.add_css_class("provider-header")

        icon_path = provider_icon_path(snapshot.provider.id)
        if icon_path:
            icon = Gtk.Image.new_from_file(str(icon_path))
        else:
            icon = Gtk.Image.new_from_icon_name("utilities-system-monitor-symbolic")
        icon.set_pixel_size(22)
        header.append(icon)

        name = Gtk.Label(label=snapshot.provider.display_name, xalign=0)
        name.add_css_class("provider-name")
        header.append(name)

        if snapshot.plan:
            plan = Gtk.Label(label=snapshot.plan, xalign=0)
            plan.add_css_class("provider-plan")
            header.append(plan)

        if snapshot.account_email:
            header.set_tooltip_text(f"Connected as {snapshot.account_email}")
        return header

    def _apply_snapshots(self, snapshots: List[ProviderSnapshot], error: Optional[str] = None) -> bool:
        self.current_snapshots = snapshots
        self._last_refresh_error = error
        self._is_refreshing = False
        self.btn_refresh.set_sensitive(True)
        self.btn_refresh.set_icon_name("view-refresh-symbolic")
        self._seconds_until_refresh = self._refresh_interval
        self._render_snapshots(snapshots)
        return False

    def _render_snapshots(self, snapshots: List[ProviderSnapshot]) -> None:
        self._clear_content()
        if not snapshots:
            message = self._last_refresh_error or "Turn on a provider to choose what to show."
            self.content_box.append(
                self._status_state("No providers", message, "dialog-information-symbolic")
            )
            return

        if self._show_total_spend:
            self.content_box.append(
                TotalSpendCard(
                    snapshots=snapshots,
                    period=self._period,
                    metric=self._metric,
                    on_period=self._on_period_changed,
                    on_metric=self._on_metric_changed,
                )
            )

        for snapshot in snapshots:
            provider_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            provider_section.add_css_class("provider-section")
            provider_section.append(self._provider_header(snapshot))
            if snapshot.is_error:
                provider_section.append(
                    self._status_state(
                        "Connection error",
                        snapshot.error or "Failed to load usage.",
                        "dialog-error-symbolic",
                    )
                )
            else:
                provider_section.append(RateLimitsGroup(snapshot.lines, history=snapshot.usage_history))
            self.content_box.append(provider_section)


def math_ceil_minutes(seconds: int) -> int:
    return max(1, (seconds + 59) // 60)
