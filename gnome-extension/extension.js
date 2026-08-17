/**
 * OpenUsage GNOME Shell extension.
 *
 * The popover replicates the macOS OpenUsage menu-bar popover: a self-styled
 * 320pt tray (light or dark, independent of the shell popup chrome) with
 * quaternary cards, a capsule period picker, a per-model spend donut, capsule
 * meters with pace ticks, collapsed detail rows behind a caret, and a footer
 * with the refresh countdown and an Options capsule.
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
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

// macOS popover geometry (pt == px at 1x).
const POPOVER_WIDTH = 320;
// 320 − content gutters (2×14) − meter row insets (2×14).
const METER_WIDTH = POPOVER_WIDTH - 4 * 14;
const RING_DIAMETER = 104;
const MIN_SLICE_SHARE = 0.025;
const MIN_FILL_WIDTH = 5; // one full circle of the 5px capsule

// Brand/model palette mirroring TotalSpendPalette in the macOS app.
const MODEL_PALETTE = {
    codex: '#10A37F',
    openai: '#10A37F',
    gpt: '#10A37F',
    claude: '#DE7356',
    cursor: { light: '#13120A', dark: '#F5F5F7' },
    grok: { light: '#8E8E93', dark: '#98989D' },
    openrouter: '#6467F2',
    copilot: '#A855F7',
};
const FALLBACK_HUES = ['#34C759', '#5856D6', '#FF2D55', '#A2845E'];

function hexToRgba(hex) {
    const value = hex.replace('#', '');
    return [
        parseInt(value.slice(0, 2), 16) / 255,
        parseInt(value.slice(2, 4), 16) / 255,
        parseInt(value.slice(4, 6), 16) / 255,
        1.0,
    ];
}

function colorForModel(name, isDark) {
    const lowered = String(name || '').toLowerCase();
    for (const [key, color] of Object.entries(MODEL_PALETTE)) {
        if (lowered.includes(key)) {
            return typeof color === 'string' ? color : (isDark ? color.dark : color.light);
        }
    }
    let hash = 0;
    for (const ch of lowered) {
        hash = ((hash * 31) + ch.codePointAt(0)) & 0xffff;
    }
    return FALLBACK_HUES[hash % FALLBACK_HUES.length];
}

function resolveOpenUsageBinary() {
    const override = GLib.getenv('OPENUSAGE_BIN');
    if (override && override.trim()) {
        return override.trim();
    }

    for (const command of ['openusage-linux', 'openusage']) {
        const resolved = GLib.find_program_in_path(command);
        if (resolved) {
            return resolved;
        }
    }

    const home = GLib.get_home_dir();
    const candidates = [
        GLib.build_filenamev([home, '.local', 'share', 'openusage', 'venv', 'bin', 'openusage-linux']),
        GLib.build_filenamev([home, '.local', 'bin', 'openusage-linux']),
        GLib.build_filenamev([home, '.local', 'bin', 'openusage']),
    ];
    for (const candidate of candidates) {
        if (GLib.file_test(candidate, GLib.FileTest.IS_EXECUTABLE)) {
            return candidate;
        }
    }

    return null;
}

function formatTokenCount(tokens) {
    if (tokens >= 1000000) {
        return `${(tokens / 1000000).toFixed(2)}M`;
    }
    if (tokens >= 1000) {
        return `${(tokens / 1000).toFixed(1)}k`;
    }
    return `${tokens}`;
}

function formatCurrency(value) {
    return `$${Number(value || 0).toFixed(2)}`;
}

function formatCompactCost(value) {
    if (value >= 1000) {
        const scaled = value / 1000;
        return `$${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1)}K`;
    }
    return formatCurrency(value);
}

function localDateKey(offsetDays = 0) {
    const current = new Date();
    current.setDate(current.getDate() + offsetDays);
    const year = current.getFullYear();
    const month = `${current.getMonth() + 1}`.padStart(2, '0');
    const day = `${current.getDate()}`.padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function periodSpend(data, period) {
    const spend = data.spend_history || {};
    if (period === 'today') {
        return {
            hasData: (spend.today_tokens || 0) > 0 || (spend.today_cost || 0) > 0,
            tokens: spend.today_tokens || 0,
            cost: spend.today_cost || 0,
            label: 'Today',
        };
    }

    if (period === 'yesterday') {
        const entry = (spend.daily_series || []).find(item => item.date === localDateKey(-1));
        return {
            hasData: Boolean(entry && ((entry.tokens || 0) > 0 || (entry.cost || 0) > 0)),
            tokens: entry?.tokens || 0,
            cost: entry?.cost || 0,
            label: 'Yesterday',
        };
    }

    return {
        hasData: (spend.total_tokens_30d || 0) > 0 || (spend.total_cost_30d || 0) > 0,
        tokens: spend.total_tokens_30d || 0,
        cost: spend.total_cost_30d || 0,
        label: '30 Days',
    };
}

function metricValue(metric, spend) {
    if (metric === 'Tokens') {
        return formatTokenCount(spend.tokens);
    }
    if (metric === 'Cost / MTok') {
        return spend.tokens > 0 ? formatCurrency(spend.cost / (spend.tokens / 1000000)) : 'No data';
    }
    return formatCurrency(spend.cost);
}

// macOS-style two-line ring center: value + small unit.
function ringCenter(metric, spend) {
    if (!spend.hasData) {
        return { value: 'No data', unit: '' };
    }
    if (metric === 'Tokens') {
        return { value: formatTokenCount(spend.tokens), unit: 'tokens' };
    }
    if (metric === 'Cost / MTok') {
        const per = spend.tokens > 0 ? spend.cost / (spend.tokens / 1000000) : 0;
        return { value: formatCurrency(per), unit: 'per MTok' };
    }
    return { value: formatCompactCost(spend.cost), unit: spend.cost >= 1000 ? 'total' : '' };
}

// Elapsed fraction of the rate-limit window, for the pace tick.
function paceFraction(limit) {
    const period = limit.period_seconds;
    if (!period || period <= 0 || !limit.resets_at) {
        return null;
    }
    const resetEpoch = Date.parse(limit.resets_at);
    if (Number.isNaN(resetEpoch)) {
        return null;
    }
    const secondsLeft = (resetEpoch - Date.now()) / 1000;
    return Math.min(1, Math.max(0, (period - secondsLeft) / period));
}

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

class SpendRing {
    constructor() {
        this.area = new St.DrawingArea({
            width: RING_DIAMETER,
            height: RING_DIAMETER,
            style_class: 'openusage-spend-ring',
        });
        this.slices = [];
        this.area.connect('repaint', area => this._repaint(area));
    }

    setSlices(slices) {
        this.slices = slices || [];
        this.area.queue_repaint();
    }

    _repaint(area) {
        const cr = area.get_context();
        const cx = RING_DIAMETER / 2;
        const cy = RING_DIAMETER / 2;
        const thickness = 13;
        const radius = (RING_DIAMETER - thickness) / 2;

        cr.setLineWidth(thickness);
        cr.setLineCap(cairo.LineCap.BUTT);

        if (this.slices.length === 0) {
            cr.setSourceRGBA(0.47, 0.47, 0.5, 0.2);
            cr.arc(cx, cy, radius, 0, 2 * Math.PI);
            cr.stroke();
        } else {
            // Hairline gaps between slices, like the macOS SectorMark ring.
            const gap = this.slices.length > 1 ? 0.75 / radius : 0;
            let start = -Math.PI / 2;
            for (const slice of this.slices) {
                const sweep = slice.share * 2 * Math.PI;
                const inset = sweep > 2 * gap ? gap : 0;
                cr.setSourceRGBA(...hexToRgba(slice.color));
                cr.arc(cx, cy, radius, start + inset, start + sweep - inset);
                cr.stroke();
                start += sweep;
            }
        }
        cr.$dispose();
    }
}

const OpenUsageIndicator = GObject.registerClass(
class OpenUsageIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'OpenUsage Indicator');
        this._extension = extension;
        this._timeoutId = null;
        this._countdownId = null;
        this._processTimeoutId = null;
        this._process = null;
        this._isRefreshing = false;
        this._latestData = null;
        this._period = 'today';
        this._metric = 'Cost';
        this._providerExpanded = false;
        this._isDark = false;
        this._secondsUntilRefresh = 60;

        this._panelBox = new St.BoxLayout({
            style_class: 'openusage-panel-box',
            vertical: false,
            y_align: Clutter.ActorAlign.CENTER,
        });
        const brandIconPath = GLib.build_filenamev([this._extension.path, 'openusage.svg']);
        this._panelIcon = GLib.file_test(brandIconPath, GLib.FileTest.EXISTS)
            ? new St.Icon({
                gicon: Gio.FileIcon.new(Gio.File.new_for_path(brandIconPath)),
                style_class: 'openusage-panel-icon',
            })
            : new St.Icon({
                icon_name: 'utilities-system-monitor-symbolic',
                style_class: 'system-status-icon openusage-panel-icon',
            });
        this._panelLabel = new St.Label({
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'openusage-panel-label',
        });
        this._panelBox.add_child(this._panelIcon);
        this._panelBox.add_child(this._panelLabel);
        this.add_child(this._panelBox);

        this._cardBox = new St.BoxLayout({
            style_class: 'openusage-popup-content',
            vertical: true,
            x_expand: true,
        });
        this._cardMenuItem = new OpenUsageCardMenuItem(this._cardBox);
        this.menu.addMenuItem(this._cardMenuItem);

        this.menu.connect('open-state-changed', (_menu, isOpen) => {
            if (isOpen && this._latestData) {
                this._updateUI(this._latestData);
            } else if (isOpen) {
                this.refreshData();
            }
        });

        this._interfaceSettings = new Gio.Settings({ schema_id: 'org.gnome.desktop.interface' });
        this._settingsChangedId = this._interfaceSettings.connect('changed', () => this._onAppearanceChanged());
        try {
            this._themeContext = St.ThemeContext.get_for_stage(global.stage);
            this._themeNotifyId = this._themeContext.connect('notify::color-scheme', () => this._onAppearanceChanged());
        } catch (error) {
            this._themeContext = null;
        }
        this._applyThemeClass();

        this._renderPlaceholder('Fetching Codex metrics…');
        this.refreshData();
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => {
            this.refreshData();
            return GLib.SOURCE_CONTINUE;
        });
        this._countdownId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            if (!this._isRefreshing) {
                this._secondsUntilRefresh = Math.max(0, this._secondsUntilRefresh - 1);
            }
            if (this._nextUpdateLabel) {
                this._nextUpdateLabel.set_text(this._formatNextUpdate());
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _onAppearanceChanged() {
        const wasDark = this._isDark;
        this._applyThemeClass();
        if (wasDark !== this._isDark && this._latestData) {
            this._updateUI(this._latestData);
        }
    }

    // The card styles itself (like the macOS tray) instead of inheriting the
    // dark shell popup chrome, so the palette follows the GNOME color scheme.
    _applyThemeClass() {
        let dark = false;
        try {
            const scheme = this._interfaceSettings.get_string('color-scheme');
            if (scheme === 'prefer-dark') {
                dark = true;
            } else {
                dark = (this._interfaceSettings.get_string('gtk-theme') || '').toLowerCase().includes('dark');
            }
        } catch (error) {
            dark = false;
        }
        this._isDark = dark;
        this._cardBox.remove_style_class_name('openusage-light');
        this._cardBox.remove_style_class_name('openusage-dark');
        this._cardBox.add_style_class_name(dark ? 'openusage-dark' : 'openusage-light');
    }

    _renderPlaceholder(message) {
        this._cardBox.destroy_all_children();
        this._nextUpdateLabel = null;
        const state = new St.BoxLayout({
            style_class: 'openusage-status-state',
            vertical: true,
            x_expand: true,
        });
        state.add_child(new St.Icon({
            icon_name: 'utilities-system-monitor-symbolic',
            style_class: 'openusage-status-icon',
        }));
        state.add_child(new St.Label({
            text: message,
            style_class: 'openusage-status-label',
            x_align: Clutter.ActorAlign.CENTER,
        }));
        this._cardBox.add_child(state);
    }

    _cancelProcessTimeout() {
        if (this._processTimeoutId) {
            GLib.source_remove(this._processTimeoutId);
            this._processTimeoutId = null;
        }
    }

    refreshData() {
        if (this._isRefreshing) {
            return;
        }
        this._isRefreshing = true;
        this._secondsUntilRefresh = 60;
        const binary = resolveOpenUsageBinary();
        if (!binary) {
            this._isRefreshing = false;
            this._renderPlaceholder('OpenUsage executable not found');
            return;
        }

        try {
            this._process = new Gio.Subprocess({
                argv: [binary, '--json'],
                flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            });
            this._process.init(null);
            this._processTimeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
                if (this._process && this._isRefreshing) {
                    this._process.force_exit();
                    this._renderPlaceholder('OpenUsage refresh timed out');
                }
                this._processTimeoutId = null;
                return GLib.SOURCE_REMOVE;
            });
            this._process.communicate_utf8_async(null, null, (proc, res) => {
                this._cancelProcessTimeout();
                this._isRefreshing = false;
                this._process = null;
                try {
                    const [ok, stdout, stderr] = proc.communicate_utf8_finish(res);
                    if (!ok || !stdout) {
                        this._renderPlaceholder(stderr?.trim() || 'Unable to read OpenUsage metrics');
                        return;
                    }
                    const data = JSON.parse(stdout.trim());
                    this._latestData = data;
                    this._updateUI(data);
                } catch (error) {
                    console.error('[OpenUsage] Failed to read metrics:', error);
                    this._renderPlaceholder('Unable to read OpenUsage metrics');
                }
            });
        } catch (error) {
            this._cancelProcessTimeout();
            this._isRefreshing = false;
            this._process = null;
            console.error('[OpenUsage] Process launch error:', error);
            this._renderPlaceholder('Unable to launch OpenUsage');
        }
    }

    _setPanelState(data) {
        const primary = data.primary_metric || {};
        const percent = primary.percentage !== undefined ? Math.round(primary.percentage) : null;
        this._panelLabel.set_text(percent === null ? '' : `${percent}%`);
        this.accessible_name = percent === null
            ? 'OpenUsage'
            : `${data.provider?.display_name || 'Codex'} · ${percent}% used`;
        for (const name of ['normal', 'warning', 'critical']) {
            this._panelLabel.remove_style_class_name(name);
        }
        this._panelLabel.add_style_class_name(data.is_error ? 'critical' : (primary.class || 'normal'));
    }

    _addSpendHeader() {
        const header = new St.BoxLayout({
            style_class: 'openusage-section-header',
            vertical: false,
            x_expand: true,
        });
        const metricButton = new St.Button({ style_class: 'openusage-text-button', can_focus: true });
        const metricBox = new St.BoxLayout({ vertical: false, style_class: 'openusage-metric-button-box' });
        metricBox.add_child(new St.Label({ text: 'Total Spend', style_class: 'openusage-section-title' }));
        metricBox.add_child(new St.Icon({ icon_name: 'pan-down-symbolic', icon_size: 9, style_class: 'openusage-section-chevron' }));
        metricButton.set_child(metricBox);
        metricButton.connect('clicked', () => {
            const metrics = ['Cost', 'Cost / MTok', 'Tokens'];
            this._metric = metrics[(metrics.indexOf(this._metric) + 1) % metrics.length];
            if (this._latestData) {
                this._updateUI(this._latestData);
            }
        });
        header.add_child(metricButton);
        header.add_child(new St.Icon({ icon_name: 'dialog-information-symbolic', icon_size: 13, style_class: 'openusage-info-icon' }));
        this._cardBox.add_child(header);
    }

    _addPeriodPicker(parent, data) {
        const picker = new St.BoxLayout({ style_class: 'openusage-period-picker', vertical: false, x_expand: true });
        for (const [key, label] of [['today', 'Today'], ['yesterday', 'Yesterday'], ['30d', '30 Days']]) {
            const button = new St.Button({ label, style_class: `openusage-period-segment${this._period === key ? ' active' : ''}`, can_focus: true });
            button.set_x_expand(true);
            button.connect('clicked', () => {
                this._period = key;
                this._updateUI(data);
            });
            picker.add_child(button);
        }
        parent.add_child(picker);
    }

    // Ranked donut slices: top 4 models by the selected metric, rest as Other.
    _spendSlices(spend, data) {
        const models = ((data.spend_history || {}).models || [])
            .map(model => ({
                name: model.model,
                amount: this._metric === 'Tokens' ? (model.tokens || 0) : (model.cost || 0),
            }))
            .filter(model => model.amount > 0)
            .sort((a, b) => b.amount - a.amount);

        const entries = [];
        if (models.length > 0) {
            const top = models.slice(0, 4);
            const restTotal = models.slice(4).reduce((sum, model) => sum + model.amount, 0);
            if (models.length > 4) {
                top.push({ name: 'Other', amount: restTotal });
            }
            const total = top.reduce((sum, model) => sum + model.amount, 0);
            if (total <= 0) {
                return [];
            }
            for (const model of top) {
                entries.push({
                    name: model.name,
                    amount: model.amount,
                    share: Math.max(MIN_SLICE_SHARE, model.amount / total),
                    color: model.name === 'Other' ? (this._isDark ? '#98989D' : '#8E8E93') : colorForModel(model.name, this._isDark),
                });
            }
        } else if (spend.hasData) {
            entries.push({
                name: data.provider?.display_name || 'Codex',
                amount: this._metric === 'Tokens' ? spend.tokens : spend.cost,
                share: 1,
                color: colorForModel('codex', this._isDark),
            });
        }

        const shareTotal = entries.reduce((sum, entry) => sum + entry.share, 0);
        if (shareTotal > 0) {
            for (const entry of entries) {
                entry.share /= shareTotal;
            }
        }
        return entries;
    }

    _addSpendCard(data) {
        const spend = periodSpend(data, this._period);
        const card = new St.BoxLayout({ style_class: 'openusage-card openusage-spend-inner', vertical: true, x_expand: true });

        this._addPeriodPicker(card, data);

        if (!spend.hasData) {
            card.add_child(new St.Label({
                text: `No data for ${spend.label.toLowerCase()}`,
                style_class: 'openusage-empty-label',
                x_align: Clutter.ActorAlign.CENTER,
            }));
            this._cardBox.add_child(card);
            return;
        }

        const slices = this._spendSlices(spend, data);

        const body = new St.BoxLayout({ style_class: 'openusage-spend-body', vertical: false, x_expand: true });
        const ringFrame = new St.Widget({ layout_manager: new Clutter.BinLayout(), width: RING_DIAMETER, height: RING_DIAMETER });
        const ring = new SpendRing();
        ring.setSlices(slices);
        ringFrame.add_child(ring.area);
        const center = new St.BoxLayout({ vertical: true, style_class: 'openusage-ring-center' });
        const centerValue = ringCenter(this._metric, spend);
        center.add_child(new St.Label({ text: centerValue.value, style_class: 'openusage-ring-value' }));
        if (centerValue.unit) {
            center.add_child(new St.Label({ text: centerValue.unit, style_class: 'openusage-ring-unit' }));
        }
        center.x_align = Clutter.ActorAlign.CENTER;
        center.y_align = Clutter.ActorAlign.CENTER;
        ringFrame.add_child(center);
        body.add_child(ringFrame);

        const legend = new St.BoxLayout({ vertical: true, style_class: 'openusage-spend-legend', x_expand: true });
        for (const slice of slices) {
            const row = new St.BoxLayout({ vertical: false, style_class: 'openusage-legend-row' });
            const dot = new St.Widget({ style_class: 'openusage-legend-dot' });
            dot.set_style(`background-color: ${slice.color};`);
            row.add_child(dot);
            row.add_child(new St.Label({ text: slice.name, style_class: 'openusage-legend-name', x_expand: true }));
            row.add_child(new St.Label({
                text: this._metric === 'Tokens' ? formatTokenCount(slice.amount) : formatCurrency(slice.amount),
                style_class: 'openusage-legend-value',
            }));
            legend.add_child(row);
        }
        body.add_child(legend);
        card.add_child(body);

        this._cardBox.add_child(card);
    }

    _addHeader(data) {
        const header = new St.BoxLayout({
            style_class: 'openusage-provider-header',
            vertical: false,
            x_expand: true,
        });
        const providerIconPath = GLib.build_filenamev([this._extension.path, 'codex.svg']);
        if (GLib.file_test(providerIconPath, GLib.FileTest.EXISTS)) {
            header.add_child(new St.Icon({
                gicon: Gio.FileIcon.new(Gio.File.new_for_path(providerIconPath)),
                icon_size: 16,
                style_class: 'openusage-provider-icon',
            }));
        }
        header.add_child(new St.Label({
            text: data.provider?.display_name || 'Codex',
            style_class: 'openusage-provider-name',
        }));
        if (data.plan) {
            header.add_child(new St.Label({ text: data.plan, style_class: 'openusage-provider-plan' }));
        }
        if (data.account_email) {
            header.accessible_name = `Connected as ${data.account_email}`;
        }

        const spacer = new St.Widget({ x_expand: true });
        header.add_child(spacer);
        const refresh = new St.Button({ style_class: 'openusage-icon-button', can_focus: true });
        refresh.set_child(new St.Icon({ icon_name: 'view-refresh-symbolic', icon_size: 13 }));
        refresh.accessible_name = 'Refresh now';
        refresh.connect('clicked', () => this.refreshData());
        header.add_child(refresh);
        this._cardBox.add_child(header);
    }

    _addMeterRow(parent, limit) {
        const used = Math.max(0, Math.min(100, limit.percentage !== undefined ? limit.percentage : (limit.used || 0)));
        const row = new St.BoxLayout({ style_class: 'openusage-meter-row', vertical: true });

        const labelRow = new St.BoxLayout({ vertical: false, style_class: 'openusage-meter-label-row' });
        labelRow.add_child(new St.Label({ text: limit.label || 'Usage', style_class: 'openusage-meter-label', x_expand: true }));
        if (used >= 100) {
            const warning = new St.BoxLayout({ vertical: false, style_class: 'openusage-meter-warning-box' });
            warning.add_child(new St.Icon({
                icon_name: 'fire-symbolic',
                icon_size: 11,
                style_class: `openusage-flame ${limit.class || 'critical'}`,
            }));
            warning.add_child(new St.Label({ text: 'Limit reached', style_class: 'openusage-meter-warning' }));
            labelRow.add_child(warning);
        }
        row.add_child(labelRow);

        const trough = new St.Widget({ layout_manager: new Clutter.BinLayout(), style_class: 'openusage-meter-trough' });
        const track = new St.Widget({ style_class: 'openusage-meter-track', x_expand: true, y_expand: true });
        trough.add_child(track);
        const fillWidth = used > 0 ? Math.max(MIN_FILL_WIDTH, Math.round(METER_WIDTH * used / 100)) : 0;
        if (fillWidth > 0) {
            const fill = new St.Widget({ style_class: `openusage-meter-fill ${limit.class || 'normal'}` });
            fill.set_width(fillWidth);
            trough.add_child(fill);
        }
        const fraction = paceFraction(limit);
        if (fraction !== null) {
            const tick = new St.Widget({ style_class: 'openusage-pace-tick', y_expand: true });
            tick.set_x_align(Clutter.ActorAlign.START);
            tick.set_width(2);
            track.add_child(tick);
            track.connect('notify::allocation', () => {
                const offset = Math.min(METER_WIDTH - 2, Math.max(0, Math.round(fraction * METER_WIDTH) - 1));
                tick.set_margin_left(offset);
            });
        }
        row.add_child(trough);

        const reading = new St.BoxLayout({ vertical: false, style_class: 'openusage-meter-reading' });
        reading.add_child(new St.Label({
            text: this._meterReading(limit, used),
            style_class: 'openusage-reading-primary',
            x_expand: true,
        }));
        reading.add_child(new St.Label({ text: limit.resets_in || '', style_class: 'openusage-reading-secondary' }));
        row.add_child(reading);
        parent.add_child(row);
    }

    _meterReading(limit, percent) {
        if (limit.format === 'dollars' && limit.limit) {
            return `$${Number(limit.used || 0).toFixed(2)} of $${Number(limit.limit).toFixed(2)}`;
        }
        if (limit.format === 'count' && limit.limit) {
            return `${Math.round(limit.used || 0)} of ${Math.round(limit.limit)}`;
        }
        return `${Math.round(100 - percent)}% left`;
    }

    _addValueRow(parent, label, detail) {
        const row = new St.BoxLayout({ style_class: 'openusage-value-row', vertical: false });
        row.add_child(new St.Label({ text: label, style_class: 'openusage-value-label', x_expand: true }));
        row.add_child(new St.Label({ text: detail, style_class: 'openusage-value', y_align: Clutter.ActorAlign.CENTER }));
        parent.add_child(row);
    }

    _addCaretToggle(parent) {
        const caret = new St.Button({ style_class: 'openusage-caret-button', can_focus: true });
        const icon = new St.Icon({
            icon_name: this._providerExpanded ? 'pan-up-symbolic' : 'pan-down-symbolic',
            icon_size: 10,
            style_class: 'openusage-caret-icon',
        });
        icon.set_x_align(Clutter.ActorAlign.CENTER);
        caret.set_child(icon);
        caret.connect('clicked', () => {
            this._providerExpanded = !this._providerExpanded;
            if (this._latestData) {
                this._updateUI(this._latestData);
            }
        });
        parent.add_child(caret);
    }

    _detailRows(data) {
        const spend = data.spend_history || {};
        const daily = spend.daily_series || [];
        const yesterday = daily.find(item => item.date === localDateKey(-1));
        const rows = [
            ['Today', (spend.today_tokens || spend.today_cost) ? `${formatCurrency(spend.today_cost)} · ${formatTokenCount(spend.today_tokens)} tokens` : 'No data'],
            ['Yesterday', yesterday && ((yesterday.tokens || 0) > 0 || (yesterday.cost || 0) > 0) ? `${formatCurrency(yesterday.cost)} · ${formatTokenCount(yesterday.tokens)} tokens` : 'No data'],
            ['Last 30 Days', (spend.total_tokens_30d || spend.total_cost_30d) ? `${formatCurrency(spend.total_cost_30d)} · ${formatTokenCount(spend.total_tokens_30d)} tokens` : 'No data'],
        ];
        for (const model of spend.models || []) {
            rows.push([model.model, `${formatCurrency(model.cost)} · ${formatTokenCount(model.tokens)}`]);
        }
        return rows;
    }

    _addProviderCard(data) {
        const card = new St.BoxLayout({ style_class: 'openusage-card openusage-metric-card', vertical: true, x_expand: true });
        for (const limit of data.rate_limits || []) {
            this._addMeterRow(card, limit);
        }
        const credits = data.credits || {};
        if (credits.rate_limit_resets !== undefined) {
            this._addValueRow(card, 'Rate Limit Resets', `${credits.rate_limit_resets} available`);
        }
        if (credits.extra_usage_credits !== undefined || credits.extra_usage_dollars !== undefined) {
            this._addValueRow(card, 'Extra Usage', `${formatCurrency(credits.extra_usage_dollars)} · ${credits.extra_usage_credits || 0} credits`);
        }

        const details = this._detailRows(data);
        if (details.length > 0) {
            this._addCaretToggle(card);
            if (this._providerExpanded) {
                for (const [label, detail] of details) {
                    this._addValueRow(card, label, detail);
                }
            }
        }

        if (card.get_n_children() === 0) {
            card.add_child(new St.Label({ text: 'No data', style_class: 'openusage-muted' }));
        }
        this._cardBox.add_child(card);
    }

    _addFooter() {
        const footer = new St.BoxLayout({ style_class: 'openusage-footer', vertical: false, x_expand: true });
        const identity = new St.BoxLayout({ vertical: true, style_class: 'openusage-footer-identity', x_expand: true });
        identity.add_child(new St.Label({ text: 'OpenUsage 0.1.0', style_class: 'openusage-footer-version' }));
        this._nextUpdateLabel = new St.Label({ text: this._formatNextUpdate(), style_class: 'openusage-footer-countdown' });
        identity.add_child(this._nextUpdateLabel);
        footer.add_child(identity);

        const openButton = new St.Button({ label: 'Options', style_class: 'openusage-options-button', can_focus: true });
        openButton.connect('clicked', () => {
            this.menu.close();
            const binary = resolveOpenUsageBinary();
            if (!binary) {
                this._renderPlaceholder('OpenUsage executable not found');
                return;
            }
            try {
                const process = new Gio.Subprocess({ argv: [binary, '--gui'], flags: Gio.SubprocessFlags.NONE });
                process.init(null);
            } catch (error) {
                console.error('[OpenUsage] Failed to launch GUI:', error);
            }
        });
        footer.add_child(openButton);
        this._cardBox.add_child(footer);
    }

    _formatNextUpdate() {
        if (this._secondsUntilRefresh >= 60) {
            return 'Next update in 1m';
        }
        return `Next update in ${this._secondsUntilRefresh}s`;
    }

    _updateUI(data) {
        this._setPanelState(data);
        this._cardBox.destroy_all_children();
        this._nextUpdateLabel = null;
        if (data.is_error) {
            this._renderPlaceholder(data.error || 'Failed to load metrics');
            return;
        }
        this._addSpendHeader();
        this._addSpendCard(data);
        this._addHeader(data);
        this._addProviderCard(data);
        this._addFooter();
    }

    destroy() {
        this._cancelProcessTimeout();
        if (this._process && this._isRefreshing) {
            this._process.force_exit();
        }
        this._process = null;
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        if (this._countdownId) {
            GLib.source_remove(this._countdownId);
            this._countdownId = null;
        }
        if (this._settingsChangedId) {
            this._interfaceSettings.disconnect(this._settingsChangedId);
            this._settingsChangedId = null;
        }
        if (this._themeContext && this._themeNotifyId) {
            this._themeContext.disconnect(this._themeNotifyId);
            this._themeNotifyId = null;
        }
        super.destroy();
    }
});

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
