"""Main Libadwaita Application Window."""

from __future__ import annotations
import threading
from datetime import datetime
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from openusage_linux.core.base import ProviderSnapshot
from openusage_linux.core.providers.codex import CodexProvider
from openusage_linux.ui.components.credits_card import CreditsGroup
from openusage_linux.ui.components.meter_card import RateLimitsGroup
from openusage_linux.ui.components.spend_card import SpendHistoryGroup


class OpenUsageWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="OpenUsage")
        self.set_default_size(460, 680)

        self.provider = CodexProvider()
        self.current_snapshot: Optional[ProviderSnapshot] = None
        self._is_refreshing = False

        # Main Toolbar View Layout
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

        # Header Bar
        self.header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(self.header_bar)

        # Header Title Widget
        self.title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.lbl_main_title = Gtk.Label(label="Codex Usage", xalign=0.5)
        self.lbl_main_title.add_css_class("heading")

        self.lbl_subtitle = Gtk.Label(label="Connecting...", xalign=0.5)
        self.lbl_subtitle.add_css_class("subtitle")
        self.title_box.append(self.lbl_main_title)
        self.title_box.append(self.lbl_subtitle)
        self.header_bar.set_title_widget(self.title_box)

        # Plan Badge in Header
        self.plan_badge = Gtk.Label(label="")
        self.plan_badge.add_css_class("plan-badge")
        self.plan_badge.set_visible(False)
        self.header_bar.pack_start(self.plan_badge)

        # Refresh Button with Spinner
        self.btn_refresh = Gtk.Button()
        self.btn_refresh.set_tooltip_text("Refresh usage metrics")
        self.btn_refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.refresh_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        self.refresh_spinner = Gtk.Spinner()
        self.refresh_spinner.set_visible(False)

        self.btn_refresh_box.append(self.refresh_icon)
        self.btn_refresh_box.append(self.refresh_spinner)
        self.btn_refresh.set_child(self.btn_refresh_box)
        self.btn_refresh.connect("clicked", self._on_refresh_clicked)
        self.header_bar.pack_end(self.btn_refresh)

        # Scrolled content container with Clamp
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_vexpand(True)
        self.toolbar_view.set_content(self.scrolled_window)

        self.clamp = Adw.Clamp()
        self.clamp.set_maximum_size(520)
        self.clamp.set_tightening_threshold(400)
        self.clamp.add_css_class("dashboard-clamp")
        self.scrolled_window.set_child(self.clamp)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.clamp.set_child(self.content_box)

        # Loading / Status placeholder
        self.status_page = Adw.StatusPage()
        self.status_page.set_title("Loading Metrics")
        self.status_page.set_description("Fetching live quotas and analyzing session logs...")
        self.status_page.set_icon_name("network-transmit-receive-symbolic")
        self.content_box.append(self.status_page)

        # Bottom refreshed time label
        self.lbl_refreshed_at = Gtk.Label(label="", xalign=0.5)
        self.lbl_refreshed_at.add_css_class("refreshed-label")
        self.lbl_refreshed_at.set_margin_top(8)

        # Start initial async load
        self.trigger_refresh()

        # Schedule 60-second periodic auto-refresh
        GLib.timeout_add_seconds(60, self._on_auto_timeout)

    def _on_refresh_clicked(self, button):
        self.trigger_refresh()

    def _on_auto_timeout(self) -> bool:
        self.trigger_refresh()
        return True  # Keep recurring

    def trigger_refresh(self):
        if self._is_refreshing:
            return
        self._is_refreshing = True
        self.refresh_icon.set_visible(False)
        self.refresh_spinner.set_visible(True)
        self.refresh_spinner.start()
        self.btn_refresh.set_sensitive(False)

        # Run refresh in background thread
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        snapshot = self.provider.refresh()
        GLib.idle_add(self._apply_snapshot, snapshot)

    def _apply_snapshot(self, snapshot: ProviderSnapshot):
        self.current_snapshot = snapshot
        self._is_refreshing = False
        self.refresh_spinner.stop()
        self.refresh_spinner.set_visible(False)
        self.refresh_icon.set_visible(True)
        self.btn_refresh.set_sensitive(True)

        # Clear content box
        while self.content_box.get_first_child():
            self.content_box.remove(self.content_box.get_first_child())

        # Update Header Subtitle & Plan
        if snapshot.account_email:
            self.lbl_subtitle.set_label(snapshot.account_email)
        else:
            self.lbl_subtitle.set_label("Connected")

        if snapshot.plan:
            self.plan_badge.set_label(snapshot.plan)
            self.plan_badge.set_visible(True)
        else:
            self.plan_badge.set_visible(False)

        if snapshot.is_error:
            error_status = Adw.StatusPage()
            error_status.set_title("Connection Error")
            error_status.set_description(snapshot.error or "Failed to load usage.")
            error_status.set_icon_name("dialog-error-symbolic")
            self.content_box.append(error_status)
            return

        # 1. Rate Limits Group
        rate_limits_group = RateLimitsGroup(snapshot.lines)
        self.content_box.append(rate_limits_group)

        # 2. Credits & Resets Group
        credits_group = CreditsGroup(snapshot.lines)
        self.content_box.append(credits_group)

        # 3. Spend & Model Analytics Group
        spend_group = SpendHistoryGroup(snapshot.usage_history)
        self.content_box.append(spend_group)

        # 4. Refreshed Timestamp
        refreshed_time_str = snapshot.refreshed_at.strftime("%I:%M:%S %p")
        self.lbl_refreshed_at.set_label(f"Last updated at {refreshed_time_str}")
        self.content_box.append(self.lbl_refreshed_at)
