/**
 * OpenUsage GNOME Shell Extension
 * Top menu bar indicator with a rich, vibrant popover card for AI subscription tracking.
 */

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';

class OpenUsageCardMenuItem extends PopupMenu.PopupBaseMenuItem {
    static {
        GObject.registerClass(this);
    }

    constructor(cardBox) {
        super({
            reactive: false,
            can_focus: false,
            activate: false,
            hover: false,
            style_class: 'openusage-card-container',
        });
        this.add_child(cardBox);
    }
}

const OpenUsageIndicator = GObject.registerClass(
class OpenUsageIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'OpenUsage Indicator');
        this._extension = extension;
        this._timeoutId = null;
        this._isRefreshing = false;
        this._latestData = null;

        // Top Bar Button Layout
        this._panelBox = new St.BoxLayout({
            style_class: 'openusage-panel-box',
            vertical: false,
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._panelIcon = new St.Icon({
            icon_name: 'utilities-system-monitor-symbolic',
            style_class: 'system-status-icon openusage-panel-icon',
        });

        this._panelLabel = new St.Label({
            text: 'Codex …',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'openusage-panel-label normal',
        });

        this._panelBox.add_child(this._panelIcon);
        this._panelBox.add_child(this._panelLabel);
        this.add_child(this._panelBox);

        // Content Card Container
        this._cardBox = new St.BoxLayout({
            style_class: 'openusage-popup-content',
            vertical: true,
            x_expand: true,
        });

        // Add Card as a PopupBaseMenuItem
        this._cardMenuItem = new OpenUsageCardMenuItem(this._cardBox);
        this.menu.addMenuItem(this._cardMenuItem);

        // Refresh when menu opens
        this.menu.connect('open-state-changed', (menu, isOpen) => {
            if (isOpen) {
                if (this._latestData) {
                    this._updateUI(this._latestData);
                } else {
                    this.refreshData();
                }
            }
        });

        // Initial placeholder render
        this._renderPlaceholder('Fetching Codex metrics…');

        // Immediate background data fetch
        this.refreshData();

        // 60-second periodic auto-refresh
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => {
            this.refreshData();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _renderPlaceholder(message) {
        this._cardBox.destroy_all_children();
        let lbl = new St.Label({
            text: message,
            x_align: Clutter.ActorAlign.CENTER,
            style: 'padding: 24px; opacity: 0.7; font-weight: bold;',
        });
        this._cardBox.add_child(lbl);
    }

    refreshData() {
        if (this._isRefreshing) return;
        this._isRefreshing = true;

        try {
            let proc = new Gio.Subprocess({
                argv: ['/home/anrahya/.local/bin/openusage-linux', '--json'],
                flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            });
            proc.init(null);

            proc.communicate_utf8_async(null, null, (proc, res) => {
                this._isRefreshing = false;
                try {
                    let [ok, stdout, stderr] = proc.communicate_utf8_finish(res);
                    if (ok && stdout) {
                        let data = JSON.parse(stdout.trim());
                        this._latestData = data;
                        this._updateUI(data);
                    }
                } catch (e) {
                    console.error('[OpenUsage] Failed to parse metrics:', e);
                }
            });
        } catch (e) {
            this._isRefreshing = false;
            console.error('[OpenUsage] Process launch error:', e);
        }
    }

    _updateUI(data) {
        // 1. Top Panel Bar Button
        let primary = data.primary_metric || {};
        let pct = primary.percentage !== undefined ? Math.round(primary.percentage) : 0;
        let labelText = `⚡ ${data.provider?.display_name || 'Codex'} ${pct}%`;
        this._panelLabel.set_text(labelText);

        let cssClass = primary.class || 'normal';
        this._panelLabel.remove_style_class_name('normal');
        this._panelLabel.remove_style_class_name('warning');
        this._panelLabel.remove_style_class_name('critical');
        this._panelLabel.add_style_class_name(cssClass);

        // 2. Dropdown Card Content
        this._cardBox.destroy_all_children();

        if (data.is_error) {
            this._renderPlaceholder(`❌ Error: ${data.error || 'Failed to load'}`);
            return;
        }

        // Header Box
        let headerBox = new St.BoxLayout({
            style_class: 'openusage-header-box',
            vertical: false,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
        });

        let titleInfoBox = new St.BoxLayout({
            vertical: true,
            x_expand: true,
        });

        let titleRow = new St.BoxLayout({ vertical: false });
        let titleLabel = new St.Label({
            text: data.provider?.display_name || 'Codex',
            style_class: 'openusage-title',
        });
        titleRow.add_child(titleLabel);

        if (data.plan) {
            let planPill = new St.Label({
                text: data.plan,
                style_class: 'openusage-plan-pill',
            });
            titleRow.add_child(planPill);
        }
        titleInfoBox.add_child(titleRow);

        if (data.account_email) {
            let emailLabel = new St.Label({
                text: data.account_email,
                style_class: 'openusage-subtitle',
            });
            titleInfoBox.add_child(emailLabel);
        }
        headerBox.add_child(titleInfoBox);

        // Refresh Button
        let refreshBtn = new St.Button({
            style_class: 'openusage-refresh-btn',
            reactive: true,
            can_focus: true,
        });
        let refreshIcon = new St.Icon({
            icon_name: 'view-refresh-symbolic',
            icon_size: 15,
        });
        refreshBtn.set_child(refreshIcon);
        refreshBtn.connect('clicked', () => {
            this.refreshData();
        });
        headerBox.add_child(refreshBtn);
        this._cardBox.add_child(headerBox);

        // Rate Limits Section
        let rateLimits = data.rate_limits || [];
        if (rateLimits.length > 0) {
            let rlSection = new St.BoxLayout({
                style_class: 'openusage-section',
                vertical: true,
            });

            let rlHeader = new St.Label({
                text: 'QUOTAS & RATE LIMITS',
                style_class: 'openusage-section-title',
            });
            rlSection.add_child(rlHeader);

            for (let rl of rateLimits) {
                let row = new St.BoxLayout({
                    style_class: 'openusage-meter-row',
                    vertical: true,
                });

                // Top label row
                let topRow = new St.BoxLayout({
                    style_class: 'openusage-meter-header',
                    vertical: false,
                    x_expand: true,
                });
                let lbl = new St.Label({
                    text: rl.label,
                    style_class: 'openusage-meter-title',
                    x_expand: true,
                });
                let pctLbl = new St.Label({
                    text: `${rl.used.toFixed(1)}%`,
                    style_class: `openusage-meter-pct ${rl.class || 'normal'}`,
                });
                topRow.add_child(lbl);
                topRow.add_child(pctLbl);
                row.add_child(topRow);

                // Progress Bar
                let trough = new St.BoxLayout({
                    style_class: 'openusage-progress-trough',
                    vertical: false,
                    x_expand: true,
                });

                let fillPct = Math.max(0, Math.min(100, rl.used));
                let fill = new St.Widget({
                    style_class: `openusage-progress-fill ${rl.class || 'normal'}`,
                    style: `width: ${Math.round((fillPct / 100.0) * 330)}px;`,
                });
                trough.add_child(fill);
                row.add_child(trough);

                // Countdown
                if (rl.resets_in) {
                    let cdLbl = new St.Label({
                        text: `🕒 Resets in ${rl.resets_in}`,
                        style_class: 'openusage-countdown-text',
                    });
                    row.add_child(cdLbl);
                }

                rlSection.add_child(row);
            }
            this._cardBox.add_child(rlSection);
        }

        // Resets & Extra Usage (if any)
        let credits = data.credits || {};
        let resetsCount = credits.rate_limit_resets || 0;
        let extraDollars = credits.extra_usage_dollars || 0.0;
        let extraCredits = credits.extra_usage_credits || 0;

        if (resetsCount > 0 || extraCredits > 0) {
            let creditsBox = new St.BoxLayout({
                style_class: 'openusage-badge-row',
                vertical: false,
                x_expand: true,
            });
            let creditsText = `🔄 ${resetsCount} reset credits available`;
            if (extraCredits > 0) {
                creditsText += ` · Extra: $${extraDollars.toFixed(2)} (${extraCredits} credits)`;
            }
            let crLabel = new St.Label({ text: creditsText, style_class: 'openusage-badge-text' });
            creditsBox.add_child(crLabel);
            this._cardBox.add_child(creditsBox);
        }

        // Spend & History Section
        let spend = data.spend_history || {};
        if (spend.today_tokens > 0 || spend.total_tokens_30d > 0) {
            let spendBox = new St.BoxLayout({
                style_class: 'openusage-section',
                vertical: true,
            });

            let spendTitle = new St.Label({
                text: 'TOKEN SPEND & ESTIMATED COST',
                style_class: 'openusage-section-title',
            });
            spendBox.add_child(spendTitle);

            // Tiles (Today vs 30 Days)
            let tilesRow = new St.BoxLayout({
                style_class: 'openusage-tiles-box',
                vertical: false,
                x_expand: true,
            });

            let tileToday = new St.BoxLayout({
                style_class: 'openusage-stat-tile',
                vertical: true,
                x_expand: true,
            });
            let todayCost = new St.Label({
                text: `$${(spend.today_cost || 0).toFixed(2)}`,
                style_class: 'openusage-stat-amount',
            });
            let todayTok = new St.Label({
                text: `Today (${formatTokenCount(spend.today_tokens || 0)} tokens)`,
                style_class: 'openusage-stat-sub',
            });
            tileToday.add_child(todayCost);
            tileToday.add_child(todayTok);
            tilesRow.add_child(tileToday);

            let tile30d = new St.BoxLayout({
                style_class: 'openusage-stat-tile',
                vertical: true,
                x_expand: true,
            });
            let cost30d = new St.Label({
                text: `$${(spend.total_cost_30d || 0).toFixed(2)}`,
                style_class: 'openusage-stat-amount',
            });
            let tok30d = new St.Label({
                text: `30 Days (${formatTokenCount(spend.total_tokens_30d || 0)} tokens)`,
                style_class: 'openusage-stat-sub',
            });
            tile30d.add_child(cost30d);
            tile30d.add_child(tok30d);
            tilesRow.add_child(tile30d);

            spendBox.add_child(tilesRow);

            // Model Breakdown
            if (spend.models && spend.models.length > 0) {
                let modelListBox = new St.BoxLayout({
                    style_class: 'openusage-model-list',
                    vertical: true,
                });
                for (let m of spend.models.slice(0, 3)) {
                    let mRow = new St.BoxLayout({
                        style_class: 'openusage-model-item',
                        vertical: false,
                        x_expand: true,
                    });
                    let dot = new St.Label({
                        text: '●',
                        style: 'color: #10a37f; margin-right: 4px; font-size: 9px;',
                    });
                    let mName = new St.Label({
                        text: m.model,
                        style_class: 'openusage-model-name',
                        x_expand: true,
                    });
                    let mCost = new St.Label({
                        text: `${formatTokenCount(m.tokens)} ($${m.cost.toFixed(2)})`,
                        style_class: 'openusage-model-cost',
                    });
                    mRow.add_child(dot);
                    mRow.add_child(mName);
                    mRow.add_child(mCost);
                    modelListBox.add_child(mRow);
                }
                spendBox.add_child(modelListBox);
            }

            this._cardBox.add_child(spendBox);
        }

        // Footer Action Button ("Open Full Dashboard")
        let footerBox = new St.BoxLayout({
            style_class: 'openusage-footer',
            vertical: true,
            x_expand: true,
        });
        let dashboardBtn = new St.Button({
            style_class: 'openusage-action-btn',
            label: 'Open Full Dashboard Window',
            reactive: true,
            can_focus: true,
        });
        dashboardBtn.connect('clicked', () => {
            this.menu.close();
            try {
                let proc = new Gio.Subprocess({
                    argv: ['/home/anrahya/.local/bin/openusage-linux', '--gui'],
                    flags: Gio.SubprocessFlags.NONE,
                });
                proc.init(null);
            } catch (e) {
                console.error('[OpenUsage] Failed to launch GUI:', e);
            }
        });
        footerBox.add_child(dashboardBtn);

        let updateFooter = new St.Label({
            text: `Refreshed at ${data.refreshed_at || ''}`,
            style_class: 'openusage-updated-footer',
            x_align: Clutter.ActorAlign.CENTER,
        });
        footerBox.add_child(updateFooter);

        this._cardBox.add_child(footerBox);
    }

    destroy() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        super.destroy();
    }
});

function formatTokenCount(tokens) {
    if (tokens >= 1000000) {
        return `${(tokens / 1000000).toFixed(2)}M`;
    } else if (tokens >= 1000) {
        return `${(tokens / 1000).toFixed(1)}k`;
    }
    return `${tokens}`;
}

export default class OpenUsageExtension extends Extension {
    enable() {
        this._indicator = new OpenUsageIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
