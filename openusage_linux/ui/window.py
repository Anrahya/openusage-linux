"""Main Libadwaita application window, shaped after the macOS OpenUsage popover."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from openusage_linux.core.base import ProviderSnapshot
from openusage_linux.core.providers.codex import CodexProvider
from openusage_linux.ui.components.meter_card import RateLimitsGroup
from openusage_linux.ui.components.spend_card import TotalSpendCard


class OpenUsageWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, provider: Optional[CodexProvider] = None):
        super().__init__(application=app, title="OpenUsage")
        self.set_default_size(420, 620)
        self.set_size_request(360, 520)

        self.provider = provider or CodexProvider()
        self.current_snapshot: Optional[ProviderSnapshot] = None
        self._is_refreshing = False
        self._seconds_until_refresh = 60

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

        version = Gtk.Label(label="OpenUsage 0.1.0", xalign=0)
        version.add_css_class("footer-label")
        footer.append(version)

        self.lbl_next_update = Gtk.Label(label="Next update in 1m", xalign=0)
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
        popover.set_child(option_box)
        options_button.set_popover(popover)
        footer.append(options_button)
        self.toolbar_view.add_bottom_bar(footer)

    def _on_refresh_option(self, _button, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.trigger_refresh()

    def _on_refresh_clicked(self, _button) -> None:
        self.trigger_refresh()

    def _on_second(self) -> bool:
        if not self._is_refreshing:
            self._seconds_until_refresh -= 1
        if self._seconds_until_refresh <= 0:
            self.trigger_refresh()
        remaining = max(0, self._seconds_until_refresh)
        if remaining >= 60:
            text = "Next update in 1m"
        else:
            text = f"Next update in {remaining}s"
        self.lbl_next_update.set_label(text)
        return True

    def trigger_refresh(self) -> None:
        if self._is_refreshing:
            return
        self._is_refreshing = True
        self._seconds_until_refresh = 60
        self.btn_refresh.set_sensitive(False)
        self.btn_refresh.set_icon_name("process-working-symbolic")
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        snapshot = self.provider.refresh()
        GLib.idle_add(self._apply_snapshot, snapshot)

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

        icon_path = Path(__file__).resolve().parents[1] / "data" / "icons" / "codex.svg"
        if icon_path.exists():
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

    def _apply_snapshot(self, snapshot: ProviderSnapshot) -> bool:
        self.current_snapshot = snapshot
        self._is_refreshing = False
        self.btn_refresh.set_sensitive(True)
        self.btn_refresh.set_icon_name("view-refresh-symbolic")
        self._seconds_until_refresh = 60
        self._clear_content()

        if snapshot.is_error:
            self.content_box.append(self._provider_header(snapshot))
            self.content_box.append(
                self._status_state(
                    "Connection error",
                    snapshot.error or "Failed to load usage.",
                    "dialog-error-symbolic",
                )
            )
            return False

        self.content_box.append(TotalSpendCard(snapshot.usage_history, provider_name=snapshot.provider.display_name))

        provider_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        provider_section.add_css_class("provider-section")
        provider_section.append(self._provider_header(snapshot))
        provider_section.append(RateLimitsGroup(snapshot.lines, history=snapshot.usage_history))
        self.content_box.append(provider_section)
        return False
