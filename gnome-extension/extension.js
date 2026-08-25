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
// macOS default order: Claude, Codex, Cursor, then everyone else alphabetically.
const PROVIDER_ORDER = ['claude', 'codex', 'cursor'];

const APP_VERSION = '0.2.1';
const PERIODS = ['today', 'yesterday', '30d'];
const METRICS = ['Cost', 'Cost / MTok', 'Tokens'];
const REFRESH_CHOICES = [30, 60, 120];

const MODEL_PALETTE = {
    codex: '#10A37F',
    openai: '#10A37F',
    gpt: '#10A37F',
    claude: '#DE7356',
    cursor: { light: '#13120A', dark: '#F5F5F7' },
    grok: { light: '#8E8E93', dark: '#98989D' },
    opencode: { light: '#6E6E73', dark: '#AEAEB2' },
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

function colorForKey(name, isDark) {
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

function colorForProvider(data, isDark) {
    return colorForKey(providerId(data) || data?.provider?.display_name, isDark);
}

function findInstalledBinary() {
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

function findPython3() {
    const fromPath = GLib.find_program_in_path('python3') || GLib.find_program_in_path('python');
    if (fromPath) {
        return fromPath;
    }
    if (GLib.file_test('/usr/bin/python3', GLib.FileTest.IS_EXECUTABLE)) {
        return '/usr/bin/python3';
    }
    return null;
}

function bundledPythonRoot(extensionPath) {
    const root = GLib.build_filenamev([extensionPath, 'python']);
    const marker = GLib.build_filenamev([root, 'openusage_linux', 'cli', 'main.py']);
    return GLib.file_test(marker, GLib.FileTest.IS_REGULAR) ? root : null;
}

function resolveOpenUsageInvocation(extensionPath, args) {
    const extra = Array.isArray(args) ? args : [];
    const override = GLib.getenv('OPENUSAGE_BIN');
    if (override && override.trim()) {
        return { argv: [override.trim(), ...extra] };
    }

    const installed = findInstalledBinary();
    if (installed) {
        return { argv: [installed, ...extra] };
    }

    const pythonRoot = bundledPythonRoot(extensionPath);
    const python = findPython3();
    if (pythonRoot && python) {
        return {
            argv: [python, '-m', 'openusage_linux', ...extra],
            pythonPath: pythonRoot,
        };
    }
    return null;
}

function missingHelperReason(extensionPath) {
    if (bundledPythonRoot(extensionPath) && !findPython3()) {
        return 'Python 3.9+ is required';
    }
    return 'OpenUsage helper not found';
}

function spawnOpenUsage(extensionPath, args, flags) {
    const invocation = resolveOpenUsageInvocation(extensionPath, args);
    if (!invocation) {
        return null;
    }
    const launcher = new Gio.SubprocessLauncher({ flags });
    const home = GLib.get_home_dir();
    if (home) {
        launcher.set_cwd(home);
    }
    launcher.setenv('PYTHONDONTWRITEBYTECODE', '1', true);
    if (invocation.pythonPath) {
        const existing = GLib.getenv('PYTHONPATH');
        launcher.setenv(
            'PYTHONPATH',
            existing ? `${invocation.pythonPath}:${existing}` : invocation.pythonPath,
            true
        );
    }
    return launcher.spawnv(invocation.argv);
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

function providerId(data) {
    return data?.provider?.id || '';
}

function compareProviders(left, right) {
    const leftId = providerId(left);
    const rightId = providerId(right);
    const leftRank = PROVIDER_ORDER.indexOf(leftId);
    const rightRank = PROVIDER_ORDER.indexOf(rightId);
    if (leftRank !== -1 || rightRank !== -1) {
        if (leftRank === -1) {
            return 1;
        }
        if (rightRank === -1) {
            return -1;
        }
        return leftRank - rightRank;
    }
    const leftName = left.provider?.display_name || leftId;
    const rightName = right.provider?.display_name || rightId;
    return leftName.localeCompare(rightName);
}

function visibleProviders(data) {
    const providers = Array.isArray(data?.providers) && data.providers.length > 0
        ? data.providers.slice()
        : (data ? [data] : []);
    return providers
        .filter(item => item && item.provider)
        .sort(compareProviders);
}

function localDateKey(offsetDays = 0) {
    const current = new Date();
    current.setDate(current.getDate() + offsetDays);
    const year = current.getFullYear();
    const month = `${current.getMonth() + 1}`.padStart(2, '0');
    const day = `${current.getDate()}`.padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function modelsFromEntry(entry) {
    return (entry?.models || []).map(model => ({
        model: model.model,
        tokens: model.tokens || 0,
        cost: model.cost || 0,
    }));
}

function spendCapableProviders(data) {
    return visibleProviders(data).filter(item => !item.is_error && item.spend_history);
}

function periodSpend(data, period) {
    const spend = data.spend_history || {};
    const daily = spend.daily_series || [];
    if (period === 'today') {
        const entry = daily.find(item => item.date === localDateKey(0));
        return {
            hasData: (spend.today_tokens || 0) > 0 || (spend.today_cost || 0) > 0,
            tokens: spend.today_tokens || 0,
            cost: spend.today_cost || 0,
            models: modelsFromEntry(entry),
            label: 'Today',
        };
    }

    if (period === 'yesterday') {
        const entry = daily.find(item => item.date === localDateKey(-1));
        return {
            hasData: Boolean(entry && ((entry.tokens || 0) > 0 || (entry.cost || 0) > 0)),
            tokens: entry?.tokens || 0,
            cost: entry?.cost || 0,
            models: modelsFromEntry(entry),
            label: 'Yesterday',
        };
    }

    return {
        hasData: (spend.total_tokens_30d || 0) > 0 || (spend.total_cost_30d || 0) > 0,
        tokens: spend.total_tokens_30d || 0,
        cost: spend.total_cost_30d || 0,
        models: spend.models || [],
        label: '30 Days',
    };
}

function combinedPeriodSpend(data, period) {
    const providers = spendCapableProviders(data).map(item => ({
        id: providerId(item),
        name: item.provider?.display_name || providerId(item) || 'Provider',
        spend: periodSpend(item, period),
    })).filter(item => item.spend.hasData);

    const tokens = providers.reduce((sum, item) => sum + (item.spend.tokens || 0), 0);
    const cost = providers.reduce((sum, item) => sum + (item.spend.cost || 0), 0);
    return {
        hasData: providers.length > 0 && (tokens > 0 || cost > 0),
        tokens,
        cost,
        label: period === 'today' ? 'Today' : (period === 'yesterday' ? 'Yesterday' : '30 Days'),
        providers,
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
        return { value: formatTokenCount(spend.tokens), unit: spend.tokens >= 1000000 ? 'million' : 'tokens' };
    }
    if (metric === 'Cost / MTok') {
        const per = spend.tokens > 0 ? spend.cost / (spend.tokens / 1000000) : 0;
        return { value: formatCurrency(per), unit: 'MTok' };
    }
    return { value: formatCompactCost(spend.cost), unit: 'dollars' };
}

function usedPercent(limit) {
    if (limit.percentage !== undefined && limit.percentage !== null) {
        return Math.max(0, Math.min(100, Number(limit.percentage)));
    }
    return Math.max(0, Math.min(100, Number(limit.used || 0)));
}

function levelClass(percent) {
    if (percent >= 90) {
        return 'critical';
    }
    if (percent >= 80) {
        return 'warning';
    }
    return 'normal';
}

function formatResetExact(resetsAt) {
    if (!resetsAt) {
        return '';
    }
    const reset = new Date(resetsAt);
    if (Number.isNaN(reset.getTime())) {
        return '';
    }
    const now = new Date();
    const sameDay = reset.toDateString() === now.toDateString();
    const tomorrow = new Date(now);
    tomorrow.setDate(now.getDate() + 1);
    const isTomorrow = reset.toDateString() === tomorrow.toDateString();
    const time = reset.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (sameDay) {
        return `Resets today at ${time}`;
    }
    if (isTomorrow) {
        return `Resets tomorrow at ${time}`;
    }
    return `Resets ${reset.toLocaleDateString([], { month: 'short', day: 'numeric' })} at ${time}`;
}

function formatLimitEta(secondsLeft) {
    if (secondsLeft <= 0) {
        return null;
    }
    const total = Math.round(secondsLeft);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days > 0) {
        return `Limit in ${days}d ${hours}h`;
    }
    if (hours > 0) {
        return `Limit in ${hours}h ${minutes}m`;
    }
    if (minutes > 0) {
        return `Limit in ${minutes}m`;
    }
    return 'Limit now';
}

// macOS pace verdict: project current burn to reset, then fall back to 80/90 bands.
function meterState(limit) {
    const percent = usedPercent(limit);
    const period = limit.period_seconds;
    const resetEpoch = limit.resets_at ? Date.parse(limit.resets_at) : NaN;
    if (percent >= 99.5) {
        return { className: 'critical', note: 'Limit reached', showTick: false, percent };
    }
    if (!period || period <= 0 || Number.isNaN(resetEpoch)) {
        return { className: levelClass(percent), note: null, showTick: false, percent };
    }

    const secondsLeft = (resetEpoch - Date.now()) / 1000;
    const elapsed = period - secondsLeft;
    const elapsedFraction = Math.min(1, Math.max(0, elapsed / period));
    if (secondsLeft <= 0 || elapsed < period * 0.02 || percent < 5) {
        return { className: levelClass(percent), note: null, showTick: false, percent };
    }

    const projected = percent / Math.max(elapsedFraction, 0.001);
    if (projected < 90) {
        return { className: 'normal', note: null, showTick: false, percent, elapsedFraction };
    }
    const spare = Math.round(100 - projected);
    if (spare >= 1) {
        return {
            className: 'warning',
            note: `~${spare}% spare`,
            showTick: true,
            percent,
            elapsedFraction,
        };
    }
    const etaSeconds = percent >= 100 ? 0 : (secondsLeft * (100 - percent)) / Math.max(projected - percent, 0.001);
    return {
        className: 'critical',
        note: formatLimitEta(etaSeconds),
        showTick: true,
        percent,
        elapsedFraction,
    };
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
        this._refreshInterval = 60;
        this._showTotalSpend = true;
        this._prefsHydrated = false;
        this._showRemaining = true;
        this._showResetCountdown = true;
        this._expandedProviders = {};
        this._isDark = false;
        this._settingsMenu = null;
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
                icon_size: 16,
                style_class: 'system-status-icon openusage-panel-icon',
            })
            : new St.Icon({
                icon_name: 'utilities-system-monitor-symbolic',
                icon_size: 16,
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

        this._trayBox = new St.BoxLayout({
            style_class: 'openusage-popup-content',
            vertical: true,
            x_expand: true,
        });
        this._scrollView = new St.ScrollView({
            style_class: 'openusage-scroll',
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
            reactive: true,
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        this._cardBox = new St.BoxLayout({
            style_class: 'openusage-popup-body',
            vertical: true,
            x_expand: true,
        });
        if (this._scrollView.set_child) {
            this._scrollView.set_child(this._cardBox);
        } else if (this._scrollView.add_actor) {
            this._scrollView.add_actor(this._cardBox);
        } else {
            this._scrollView.add_child(this._cardBox);
        }
        this._trayBox.clip_to_allocation = true;
        this._footerBox = new St.BoxLayout({
            style_class: 'openusage-footer',
            vertical: false,
            x_expand: true,
        });
        this._trayBox.add_child(this._scrollView);
        this._trayBox.add_child(this._footerBox);
        this._cardMenuItem = new OpenUsageCardMenuItem(this._trayBox);
        this.menu.addMenuItem(this._cardMenuItem);

        this.menu.connect('open-state-changed', (_menu, isOpen) => {
            if (isOpen) {
                this._constrainPopoverHeight();
            }
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

        this._renderPlaceholder('Fetching OpenUsage metrics…');
        this._addFooter();
        this.refreshData();
        this._restartRefreshTimer();
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
        this._trayBox.remove_style_class_name('openusage-light');
        this._trayBox.remove_style_class_name('openusage-dark');
        this._trayBox.add_style_class_name(dark ? 'openusage-dark' : 'openusage-light');
    }

    // Panel menus set max-height on the chrome, but a tall BoxLayout still
    // reports its natural height and gets clipped. Bound the ScrollView so
    // extra provider cards can actually move.
    _constrainPopoverHeight() {
        const monitor = Main.layoutManager.findMonitorForActor(this)
            || Main.layoutManager.primaryMonitor;
        if (!monitor) {
            return;
        }
        const workArea = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        let scale = 1;
        try {
            scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        } catch (error) {
            scale = 1;
        }
        const verticalMargins = (this.menu.actor.margin_top || 0)
            + (this.menu.actor.margin_bottom || 0);
        const footerHeight = Math.max(this._footerBox.height || 0, 52);
        const maxHeight = Math.max(240, Math.round((workArea.height - verticalMargins) / scale) - 16 - footerHeight);
        this._scrollView.style = `max-height: ${maxHeight}px;`;
    }

    _renderPlaceholder(message) {
        this._cardBox.destroy_all_children();
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
        this._secondsUntilRefresh = this._refreshInterval;
        try {
            const process = spawnOpenUsage(
                this._extension.path,
                ['--json'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );
            if (!process) {
                this._isRefreshing = false;
                this._renderPlaceholder(missingHelperReason(this._extension.path));
                return;
            }
            this._process = process;
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
                    let data;
                    try {
                        data = JSON.parse(stdout.trim());
                    } catch (parseError) {
                        console.error('[OpenUsage] Failed to parse metrics:', parseError);
                        this._renderPlaceholder('OpenUsage did not return JSON. Try `openusage-linux --enable codex`.');
                        return;
                    }
                    this._latestData = data;
                    this._applyPrefs(data);
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

    _addSpendHeader(data) {
        const header = new St.BoxLayout({
            style_class: 'openusage-section-header',
            vertical: false,
            x_expand: true,
        });
        const metricButton = new St.Button({ style_class: 'openusage-text-button', can_focus: true });
        const metricBox = new St.BoxLayout({ vertical: false, style_class: 'openusage-metric-button-box' });
        metricBox.add_child(new St.Label({ text: this._metric, style_class: 'openusage-section-title' }));
        metricBox.add_child(new St.Icon({ icon_name: 'pan-down-symbolic', icon_size: 9, style_class: 'openusage-section-chevron' }));
        metricButton.set_child(metricBox);
        metricButton.accessible_name = 'Total Spend Metric';
        metricButton.connect('clicked', () => {
            this._metric = METRICS[(METRICS.indexOf(this._metric) + 1) % METRICS.length];
            this._persistPref('metric', this._metric);
            if (this._latestData) {
                this._updateUI(this._latestData);
            }
        });
        header.add_child(metricButton);
        const info = new St.Icon({ icon_name: 'dialog-information-symbolic', icon_size: 13, style_class: 'openusage-info-icon' });
        const names = spendCapableProviders(data).map(item => item.provider?.display_name).filter(Boolean);
        info.accessible_name = names.length > 0
            ? `Only includes ${names.join(' and ')}.`
            : 'Total spend across enabled providers.';
        header.add_child(info);
        this._cardBox.add_child(header);
    }

    _addPeriodPicker(parent, data) {
        const picker = new St.BoxLayout({ style_class: 'openusage-period-picker', vertical: false, x_expand: true });
        for (const [key, label] of [['today', 'Today'], ['yesterday', 'Yesterday'], ['30d', '30 Days']]) {
            const button = new St.Button({ label, style_class: `openusage-period-segment${this._period === key ? ' active' : ''}`, can_focus: true });
            button.set_x_expand(true);
            button.connect('clicked', () => {
                this._period = key;
                this._persistPref('period', key);
                this._updateUI(data);
            });
            picker.add_child(button);
        }
        parent.add_child(picker);
    }

    _sliceAmount(spend) {
        if (this._metric === 'Tokens') {
            return spend.tokens || 0;
        }
        if (this._metric === 'Cost / MTok') {
            return spend.tokens > 0 ? spend.cost / (spend.tokens / 1000000) : 0;
        }
        return spend.cost || 0;
    }

    _spendSlices(spend, data) {
        const providers = (spend.providers || []).map(item => ({
            name: item.name,
            amount: this._sliceAmount(item.spend),
            color: colorForProvider({ provider: { id: item.id, display_name: item.name } }, this._isDark),
        })).filter(item => item.amount > 0)
            .sort((left, right) => right.amount - left.amount);

        const entries = [];
        if (providers.length > 0) {
            const total = providers.reduce((sum, item) => sum + item.amount, 0);
            if (total <= 0) {
                return [];
            }
            for (const item of providers) {
                entries.push({
                    name: item.name,
                    amount: item.amount,
                    share: Math.max(MIN_SLICE_SHARE, item.amount / total),
                    color: item.color,
                });
            }
        } else if (spend.hasData) {
            entries.push({
                name: data.provider?.display_name || 'OpenUsage',
                amount: this._sliceAmount(spend),
                share: 1,
                color: colorForProvider(data, this._isDark),
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
        const spend = combinedPeriodSpend(data, this._period);
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

    _providerIconPath(data) {
        const id = providerId(data);
        if (!id) {
            return null;
        }
        const named = GLib.build_filenamev([this._extension.path, `${id}.svg`]);
        return GLib.file_test(named, GLib.FileTest.EXISTS) ? named : null;
    }

    _addHeader(parent, data) {
        const header = new St.BoxLayout({
            style_class: 'openusage-provider-header',
            vertical: false,
            x_expand: true,
        });
        const providerIconPath = this._providerIconPath(data);
        if (providerIconPath) {
            const well = new St.BoxLayout({
                style_class: 'openusage-provider-icon-well',
                y_align: Clutter.ActorAlign.CENTER,
            });
            well.add_child(new St.Icon({
                gicon: Gio.FileIcon.new(Gio.File.new_for_path(providerIconPath)),
                icon_size: 14,
                style_class: 'openusage-provider-icon',
            }));
            header.add_child(well);
        }
        header.add_child(new St.Label({
            text: data.provider?.display_name || 'Provider',
            style_class: 'openusage-provider-name',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        if (data.plan) {
            header.add_child(new St.Label({
                text: data.plan,
                style_class: 'openusage-provider-plan',
                y_align: Clutter.ActorAlign.CENTER,
            }));
        }
        if (data.account_email) {
            header.accessible_name = `Connected as ${data.account_email}`;
        }
        parent.add_child(header);
    }

    _addMeterRow(parent, limit) {
        const state = meterState(limit);
        const used = state.percent;
        const row = new St.BoxLayout({ style_class: 'openusage-meter-row', vertical: true });

        const labelRow = new St.BoxLayout({ vertical: false, style_class: 'openusage-meter-label-row' });
        labelRow.add_child(new St.Label({ text: limit.label || 'Usage', style_class: 'openusage-meter-label', x_expand: true }));
        if (state.note) {
            const warning = new St.BoxLayout({ vertical: false, style_class: 'openusage-meter-warning-box' });
            if (state.className === 'critical') {
                warning.add_child(new St.Icon({
                    icon_name: 'fire-symbolic',
                    icon_size: 11,
                    style_class: `openusage-flame ${state.className}`,
                }));
            }
            warning.add_child(new St.Label({ text: state.note, style_class: 'openusage-meter-warning' }));
            labelRow.add_child(warning);
        }
        row.add_child(labelRow);

        const trough = new St.Widget({ layout_manager: new Clutter.BinLayout(), style_class: 'openusage-meter-trough' });
        const track = new St.Widget({ style_class: 'openusage-meter-track' });
        track.set_x_expand(true);
        track.set_y_expand(true);
        trough.add_child(track);
        const fillWidth = used > 0 ? Math.max(MIN_FILL_WIDTH, Math.round(METER_WIDTH * used / 100)) : 0;
        if (fillWidth > 0) {
            const fill = new St.Widget({ style_class: `openusage-meter-fill ${state.className}` });
            // BinLayout defaults to CENTER — constructor x_align is ignored on St.Widget,
            // which made the capsule float in the middle of the trough.
            fill.set_x_align(Clutter.ActorAlign.START);
            fill.set_y_align(Clutter.ActorAlign.FILL);
            fill.set_x_expand(false);
            fill.set_width(fillWidth);
            trough.add_child(fill);
        }
        if (state.showTick && state.elapsedFraction !== undefined) {
            const tick = new St.Widget({ style_class: 'openusage-pace-tick' });
            tick.set_x_align(Clutter.ActorAlign.START);
            tick.set_y_align(Clutter.ActorAlign.FILL);
            tick.set_x_expand(false);
            tick.set_width(2);
            trough.add_child(tick);
            const offset = Math.min(METER_WIDTH - 2, Math.max(0, Math.round(state.elapsedFraction * METER_WIDTH) - 1));
            tick.set_margin_left(offset);
        }
        row.add_child(trough);

        const reading = new St.BoxLayout({ vertical: false, style_class: 'openusage-meter-reading' });
        const usedButton = new St.Button({
            label: this._meterReading(limit, used),
            style_class: 'openusage-text-button openusage-reading-primary',
            can_focus: true,
            x_expand: true,
            x_align: Clutter.ActorAlign.START,
        });
        usedButton.connect('clicked', () => {
            this._showRemaining = !this._showRemaining;
            if (this._latestData) {
                this._updateUI(this._latestData);
            }
        });
        reading.add_child(usedButton);

        const resetText = this._resetReading(limit);
        if (resetText) {
            const resetButton = new St.Button({
                label: resetText,
                style_class: 'openusage-text-button openusage-reading-secondary',
                can_focus: true,
            });
            resetButton.connect('clicked', () => {
                this._showResetCountdown = !this._showResetCountdown;
                if (this._latestData) {
                    this._updateUI(this._latestData);
                }
            });
            reading.add_child(resetButton);
        }
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
        if (this._showRemaining) {
            return `${Math.round(100 - percent)}% left`;
        }
        return `${Math.round(percent)}% used`;
    }

    _resetReading(limit) {
        if (this._showResetCountdown) {
            return limit.resets_in || '';
        }
        return formatResetExact(limit.resets_at);
    }

    _addValueRow(parent, label, detail) {
        const row = new St.BoxLayout({ style_class: 'openusage-value-row', vertical: false });
        row.add_child(new St.Label({ text: label, style_class: 'openusage-value-label', x_expand: true }));
        row.add_child(new St.Label({ text: detail, style_class: 'openusage-value', y_align: Clutter.ActorAlign.CENTER }));
        parent.add_child(row);
    }

    _isProviderExpanded(data) {
        return Boolean(this._expandedProviders[providerId(data)]);
    }

    _addCaretToggle(parent, data) {
        const expanded = this._isProviderExpanded(data);
        const caret = new St.Button({ style_class: 'openusage-caret-button', can_focus: true });
        const icon = new St.Icon({
            icon_name: expanded ? 'pan-up-symbolic' : 'pan-down-symbolic',
            icon_size: 10,
            style_class: 'openusage-caret-icon',
        });
        icon.set_x_align(Clutter.ActorAlign.CENTER);
        caret.set_child(icon);
        caret.accessible_name = expanded ? 'Hide extra metrics' : 'Show extra metrics';
        caret.connect('clicked', () => {
            const id = providerId(data);
            this._expandedProviders[id] = !this._expandedProviders[id];
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
        this._addHeader(card, data);
        if (data.is_error) {
            card.add_child(new St.Label({
                text: data.error || 'No data',
                style_class: 'openusage-muted openusage-card-message',
            }));
            this._cardBox.add_child(card);
            return;
        }
        for (const limit of data.rate_limits || []) {
            this._addMeterRow(card, limit);
        }
        const credits = data.credits || {};
        if (credits.rate_limit_resets) {
            this._addValueRow(card, 'Rate Limit Resets', `${credits.rate_limit_resets} available`);
        }
        if (credits.credits_dollars !== undefined) {
            this._addValueRow(card, 'Credits', `${formatCurrency(credits.credits_dollars)} left`);
        }
        if ((credits.bonus_dollars || 0) > 0) {
            this._addValueRow(card, 'Bonus usage', formatCurrency(credits.bonus_dollars));
        }
        if (credits.pay_as_you_go) {
            this._addValueRow(card, 'Pay as you go', credits.pay_as_you_go);
        } else if ((credits.extra_usage_credits || 0) > 0 || (credits.extra_usage_dollars || 0) > 0) {
            this._addValueRow(card, 'Extra Usage', `${formatCurrency(credits.extra_usage_dollars)} · ${credits.extra_usage_credits || 0} credits`);
        }

        const details = this._detailRows(data);
        if (details.length > 0) {
            this._addCaretToggle(card, data);
            if (this._isProviderExpanded(data)) {
                for (const [label, detail] of details) {
                    this._addValueRow(card, label, detail);
                }
            }
        }

        if (card.get_n_children() === 1) {
            card.add_child(new St.Label({ text: 'No data', style_class: 'openusage-muted openusage-card-message' }));
        }
        this._cardBox.add_child(card);
    }

    _addFooter() {
        this._footerBox.destroy_all_children();
        const identity = new St.BoxLayout({ vertical: true, style_class: 'openusage-footer-identity', x_expand: true });
        identity.add_child(new St.Label({ text: `OpenUsage ${APP_VERSION}`, style_class: 'openusage-footer-version' }));
        const refreshNow = new St.Button({ style_class: 'openusage-text-button', can_focus: true, x_align: Clutter.ActorAlign.START });
        this._nextUpdateLabel = new St.Label({ text: this._formatNextUpdate(), style_class: 'openusage-footer-countdown' });
        refreshNow.set_child(this._nextUpdateLabel);
        refreshNow.accessible_name = 'Refresh now';
        refreshNow.connect('clicked', () => this.refreshData());
        identity.add_child(refreshNow);
        this._footerBox.add_child(identity);

        const actions = new St.BoxLayout({ style_class: 'openusage-footer-actions', vertical: false });
        const refresh = new St.Button({ style_class: 'openusage-icon-button', can_focus: true });
        refresh.set_child(new St.Icon({ icon_name: 'view-refresh-symbolic', icon_size: 13 }));
        refresh.accessible_name = 'Refresh now';
        refresh.connect('clicked', () => this.refreshData());
        actions.add_child(refresh);
        const openButton = new St.Button({ label: 'Options', style_class: 'openusage-options-button', can_focus: true });
        openButton.connect('clicked', () => this._openSettingsMenu());
        actions.add_child(openButton);
        this._footerBox.add_child(actions);
    }

    _openSettingsMenu() {
        if (this._settingsMenu) {
            this._settingsMenu.destroy();
            this._settingsMenu = null;
            return;
        }

        const settingsMenu = new PopupMenu.PopupMenu(this, 0.0, St.Side.TOP);
        settingsMenu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        settingsMenu.addMenuItem(this._settingsTitleItem());

        const available = (this._latestData && this._latestData.available_providers) || [];
        if (available.length === 0) {
            const empty = new PopupMenu.PopupMenuItem('No providers detected', { reactive: false });
            settingsMenu.addMenuItem(empty);
        }
        for (const provider of available) {
            const item = new PopupMenu.PopupSwitchMenuItem(provider.display_name || provider.id, provider.enabled !== false);
            item.connect('toggled', (_item, state) => this._toggleProvider(provider.id, state));
            settingsMenu.addMenuItem(item);
        }

        settingsMenu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const spendItem = new PopupMenu.PopupSwitchMenuItem('Show Total Spend', this._showTotalSpend);
        spendItem.connect('toggled', (_item, state) => this._setShowTotalSpend(state));
        settingsMenu.addMenuItem(spendItem);

        for (const seconds of REFRESH_CHOICES) {
            const item = new PopupMenu.PopupMenuItem(`Refresh every ${seconds}s`);
            if (this._refreshInterval === seconds) {
                item.setOrnament(PopupMenu.Ornament.DOT);
            }
            item.connect('activate', () => this._setRefreshInterval(seconds));
            settingsMenu.addMenuItem(item);
        }

        settingsMenu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const windowItem = new PopupMenu.PopupMenuItem('Open OpenUsage Window');
        windowItem.connect('activate', () => {
            this.menu.close();
            try {
                spawnOpenUsage(
                    this._extension.path,
                    ['--gui'],
                    Gio.SubprocessFlags.NONE
                );
            } catch (error) {
                console.error('[OpenUsage] Failed to launch GUI:', error);
            }
        });
        settingsMenu.addMenuItem(windowItem);

        this.menu.box.add_child(settingsMenu.actor);
        settingsMenu.open(true);
        this._settingsMenu = settingsMenu;
        this.menu.connect_once('open-state-changed', (_menu, isOpen) => {
            if (!isOpen && this._settingsMenu) {
                this._settingsMenu.destroy();
                this._settingsMenu = null;
            }
        });
    }

    _settingsTitleItem() {
        const item = new PopupMenu.PopupMenuItem('Show or hide providers', { reactive: false, style_class: 'openusage-settings-title' });
        return item;
    }

    _toggleProvider(providerId, enable) {
        try {
            const process = spawnOpenUsage(
                this._extension.path,
                [enable ? '--enable' : '--disable', providerId],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );
            if (!process) {
                return;
            }
            process.communicate_utf8_async(null, null, (proc, res) => {
                try {
                    proc.communicate_utf8_finish(res);
                } catch (error) {
                    console.error('[OpenUsage] Failed to toggle provider:', error);
                }
                if (this._settingsMenu) {
                    this._settingsMenu.destroy();
                    this._settingsMenu = null;
                }
                this.refreshData();
            });
        } catch (error) {
            console.error('[OpenUsage] Failed to launch toggle:', error);
        }
    }

    _applyPrefs(data) {
        if (this._prefsHydrated) {
            return;
        }
        const prefs = data?.prefs || {};
        if (PERIODS.includes(prefs.period)) {
            this._period = prefs.period;
        }
        if (METRICS.includes(prefs.metric)) {
            this._metric = prefs.metric;
        }
        if (typeof prefs.refresh_interval === 'number' && prefs.refresh_interval >= 5) {
            this._refreshInterval = prefs.refresh_interval;
            this._secondsUntilRefresh = this._refreshInterval;
            this._restartRefreshTimer();
        }
        if (typeof prefs.show_total_spend === 'boolean') {
            this._showTotalSpend = prefs.show_total_spend;
        }
        this._prefsHydrated = true;
    }

    _persistPref(key, value) {
        try {
            const process = spawnOpenUsage(
                this._extension.path,
                ['--set-pref', `${key}=${value}`],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );
            if (!process) {
                return;
            }
            process.communicate_utf8_async(null, null, (proc, res) => {
                try {
                    proc.communicate_utf8_finish(res);
                } catch (error) {
                    console.error('[OpenUsage] Failed to save preference:', error);
                }
            });
        } catch (error) {
            console.error('[OpenUsage] Failed to launch preference save:', error);
        }
    }

    _setShowTotalSpend(enabled) {
        this._showTotalSpend = enabled;
        this._persistPref('show_total_spend', enabled ? 'true' : 'false');
        if (this._latestData) {
            this._updateUI(this._latestData);
        }
    }

    _setRefreshInterval(seconds) {
        this._refreshInterval = seconds;
        this._secondsUntilRefresh = seconds;
        this._persistPref('refresh_interval', String(seconds));
        this._restartRefreshTimer();
        if (this._nextUpdateLabel) {
            this._nextUpdateLabel.set_text(this._formatNextUpdate());
        }
    }

    _restartRefreshTimer() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, this._refreshInterval, () => {
            this.refreshData();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _formatNextUpdate() {
        if (this._secondsUntilRefresh >= 60) {
            return `Next update in ${Math.ceil(this._secondsUntilRefresh / 60)}m`;
        }
        return `Next update in ${this._secondsUntilRefresh}s`;
    }

    _updateUI(data) {
        this._setPanelState(data);
        this._cardBox.destroy_all_children();
        const providers = visibleProviders(data);
        if (providers.length === 0) {
            this._renderPlaceholder(data.error || 'Turn on a provider to choose what to show.');
            this._addFooter();
            return;
        }
        if (this._showTotalSpend) {
            this._addSpendHeader(data);
            this._addSpendCard(data);
        }
        for (const provider of providers) {
            this._addProviderCard(provider);
        }
        this._addFooter();
    }

    destroy() {
        if (this._settingsMenu) {
            this._settingsMenu.destroy();
            this._settingsMenu = null;
        }
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
