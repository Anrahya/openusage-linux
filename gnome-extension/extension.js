/**
 * OpenUsage GNOME Shell Extension
 * Top menu bar indicator with a rich, visually stunning popover card and circular gauge.
 */

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import cairo from 'gi://cairo';

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

        // Top Bar Button: Icon + Percentage Label
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
            style: 'padding: 28px; opacity: 0.7; font-weight: bold;',
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

        // 3. Hero Section: Circular Gauge + Live Quota Info
        let heroCard = new St.BoxLayout({
            style_class: 'openusage-hero-card',
            vertical: false,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
        });

        // Cairo Circular Ring Gauge
        let gaugeArea = new St.DrawingArea({
            width: 110,
            height: 110,
            style_class: 'openusage-gauge-container',
        });

        let primaryPctClamped = Math.max(0, Math.min(100, primary.percentage || 0));

        gaugeArea.connect('repaint', (area) => {
            let cr = area.get_context();
            let cx = 55;
            let cy = 55;
            let radius = 42;
            let lineWidth = 9;

            // Background Track Circle
            cr.setLineWidth(lineWidth);
            cr.setSourceRGBA(1.0, 1.0, 1.0, 0.12);
            cr.arc(cx, cy, radius, 0, 2 * Math.PI);
            cr.stroke();

            // Active Percentage Arc
            let startAngle = -Math.PI / 2;
            let sweep = (primaryPctClamped / 100.0) * 2 * Math.PI;
            let endAngle = startAngle + sweep;

            if (sweep > 0) {
                // Color based on threshold
                if (primaryPctClamped >= 90.0) {
                    cr.setSourceRGBA(0.93, 0.27, 0.27, 1.0); // Red
                } else if (primaryPctClamped >= 75.0) {
                    cr.setSourceRGBA(0.96, 0.62, 0.04, 1.0); // Amber
                } else {
                    cr.setSourceRGBA(0.06, 0.72, 0.50, 1.0); // Emerald
                }

                cr.setLineWidth(lineWidth);
                cr.arc(cx, cy, radius, startAngle, endAngle);
                cr.stroke();
            }

            // Center Text: "68%"
            cr.setSourceRGBA(1.0, 1.0, 1.0, 1.0);
            try {
                cr.selectFontFace("Sans", 0, 1);
            } catch (e) {
                // Ignore font face selection error fallback
            }
            cr.setFontSize(22);
            
            let pctString = `${Math.round(primaryPctClamped)}%`;
            let extents = cr.textExtents(pctString);
            cr.moveTo(cx - (extents.width / 2), cy + (extents.height / 2) - 3);
            cr.showText(pctString);

            // Center Subtitle: "WEEKLY"
            cr.setSourceRGBA(1.0, 1.0, 1.0, 0.55);
            cr.setFontSize(9);
            let subString = "WEEKLY";
            let subExtents = cr.textExtents(subString);
            cr.moveTo(cx - (subExtents.width / 2), cy + (extents.height / 2) + 12);
            cr.showText(subString);

            cr.$dispose();
        });

        heroCard.add_child(gaugeArea);

        // Hero Details on the right of the gauge
        let heroDetails = new St.BoxLayout({
            style_class: 'openusage-hero-details',
            vertical: true,
            x_expand: true,
        });

        let heroTitle = new St.Label({
            text: 'Weekly Quota Limit',
            style_class: 'openusage-hero-title',
        });
        heroDetails.add_child(heroTitle);

        if (primary.resets_in) {
            let cdPill = new St.Label({
                text: `🕒 Resets in ${primary.resets_in}`,
                style_class: 'openusage-countdown-pill',
            });
            heroDetails.add_child(cdPill);
        }

        // Secondary / Spark Limit mini row
        let rateLimits = data.rate_limits || [];
        let sparkLimit = rateLimits.find(r => r.label && r.label.toLowerCase().includes('spark'));
        if (sparkLimit) {
            let sparkRow = new St.BoxLayout({ vertical: false, style: 'margin-top: 4px;' });
            let sparkLbl = new St.Label({
                text: `Spark: ${sparkLimit.used.toFixed(1)}% used`,
                style: 'font-size: 11px; opacity: 0.75; font-weight: 600;',
            });
            sparkRow.add_child(sparkLbl);
            heroDetails.add_child(sparkRow);
        }

        heroCard.add_child(heroDetails);
        this._cardBox.add_child(heroCard);

        // 4. Token Distribution Visual Card (Segmented Proportional Bar)
        let spend = data.spend_history || {};
        let todayTokens = spend.today_tokens || 0;
        let todayInput = spend.today_input || 0;
        let todayCached = spend.today_cached || 0;
        let todayOutput = spend.today_output || 0;
        let uncachedInput = Math.max(0, todayInput - todayCached);
        let cacheRate = spend.cache_hit_rate || 0.0;

        if (todayTokens > 0) {
            let tokenCard = new St.BoxLayout({
                style_class: 'openusage-token-card',
                vertical: true,
            });

            // Card Header
            let tokenCardHeader = new St.BoxLayout({ vertical: false, x_expand: true });
            let tokenCardTitle = new St.Label({
                text: 'TOKEN USAGE DISTRIBUTION',
                style_class: 'openusage-section-title',
                x_expand: true,
            });
            let effBadge = new St.Label({
                text: `⚡ ${cacheRate.toFixed(1)}% Cached`,
                style_class: 'openusage-efficiency-badge',
            });
            tokenCardHeader.add_child(tokenCardTitle);
            tokenCardHeader.add_child(effBadge);
            tokenCard.add_child(tokenCardHeader);

            // Proportional Segmented Visual Bar
            let totalBarWidth = 330;
            let uncachedW = Math.max(8, Math.round((uncachedInput / todayTokens) * totalBarWidth));
            let cachedW = Math.max(8, Math.round((todayCached / todayTokens) * totalBarWidth));
            let outputW = Math.max(6, totalBarWidth - uncachedW - cachedW);

            let segmentedBar = new St.BoxLayout({
                style_class: 'openusage-segmented-bar-trough',
                vertical: false,
                x_expand: true,
            });

            let segUncached = new St.Widget({
                style_class: 'openusage-segment-uncached',
                style: `width: ${uncachedW}px;`,
            });
            let segCached = new St.Widget({
                style_class: 'openusage-segment-cached',
                style: `width: ${cachedW}px;`,
            });
            let segOutput = new St.Widget({
                style_class: 'openusage-segment-output',
                style: `width: ${outputW}px;`,
            });

            segmentedBar.add_child(segUncached);
            segmentedBar.add_child(segCached);
            segmentedBar.add_child(segOutput);
            tokenCard.add_child(segmentedBar);

            // Segment Legend Row
            let legendRow = new St.BoxLayout({
                style_class: 'openusage-token-legend',
                vertical: false,
                x_expand: true,
            });

            // Legend item 1: Uncached
            let leg1 = new St.BoxLayout({ vertical: false, style_class: 'openusage-legend-item' });
            leg1.add_child(new St.Label({ text: '●', style_class: 'openusage-dot-uncached' }));
            leg1.add_child(new St.Label({ text: ` Input ${formatTokenCount(uncachedInput)}` }));
            legendRow.add_child(leg1);

            // Legend item 2: Cached
            let leg2 = new St.BoxLayout({ vertical: false, style_class: 'openusage-legend-item' });
            leg2.add_child(new St.Label({ text: '●', style_class: 'openusage-dot-cached' }));
            leg2.add_child(new St.Label({ text: ` Cached ${formatTokenCount(todayCached)}` }));
            legendRow.add_child(leg2);

            // Legend item 3: Output
            let leg3 = new St.BoxLayout({ vertical: false, style_class: 'openusage-legend-item' });
            leg3.add_child(new St.Label({ text: '●', style_class: 'openusage-dot-output' }));
            leg3.add_child(new St.Label({ text: ` Out ${formatTokenCount(todayOutput)}` }));
            legendRow.add_child(leg3);

            tokenCard.add_child(legendRow);
            this._cardBox.add_child(tokenCard);
        }

        // 5. Stat Tiles: Today vs 30 Days Total Cost
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

        this._cardBox.add_child(tilesRow);

        // 6. Model Breakdown Chips
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
                    style: 'color: #34d399; margin-right: 4px; font-size: 10px;',
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
            this._cardBox.add_child(modelListBox);
        }

        // 7. Footer Action Button
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
