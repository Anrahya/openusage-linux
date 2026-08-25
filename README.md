# OpenUsage for Linux

A native Linux (GNOME / Wayland) port of [OpenUsage](https://github.com/robinebers/openusage) for tracking AI subscription quotas, rate limits, reset credits, and token usage.

Runs on any distro with GNOME Shell 45+ and Python 3.9+: a **top-bar menu-bar extension** (the main experience, exactly like the macOS app), a fast CLI with Waybar integration, and an optional GTK4/Libadwaita desktop window.

**Using an AI agent?** Paste this repo URL and tell it to set OpenUsage up. It should follow [`AGENTS.md`](AGENTS.md) (install playbook first; no tokens to paste).

---

## 🧩 Supported providers

Providers are detected automatically — any tool you're already logged into shows up in the top bar and CLI, no configuration needed:

| Provider | Status | Credential source |
|----------|--------|-------------------|
| **Codex**    | ✅ Verified | `~/.codex/auth.json` (Codex CLI login) |
| **Claude**   | 🧪 Needs verification | `~/.claude/.credentials.json` (Claude Code login) |
| **Cursor**   | ✅ Verified live (Pro+) | `~/.config/Cursor/User/globalStorage/state.vscdb` (Cursor app login) |
| **OpenCode** | ✅ Verified | `~/.local/share/opencode` (`auth.json` or `opencode.db` credential + session logs) |
| **Grok**     | ✅ Weekly pool verified | `~/.grok/auth.json` (`grok login`) or OpenCode `opencode.db` `xai` OAuth |

Quota meters come from each provider's usage API; token & spend history comes from your local session logs (Codex/Claude/Grok) or provider exports (Cursor) — ported from the upstream macOS app's provider logic.

---

## ✨ Features

- 📊 **Live Quota & Rate Limit Tracking**:
  - **Session (5-hour) & Weekly Limits**: Live percentage used, progress meters, and dynamic countdown timers.
  - **Spark & Model-Specific Limits**: Supports `GPT-5.3-Codex-Spark` and custom rate limits.
  - **Rate Limit Reset Credits**: Tracks available on-demand resets and per-credit expiry dates.
  - **Extra Usage / Flex Credits**: Tracks remaining flex credits and dollar balance ($0.04/credit).
- 🔄 **Automatic Token Rotation**:
  - Automatically inspects OAuth JWT tokens (`~/.codex/auth.json` or `CODEX_HOME`) and proactively refreshes expiring tokens with atomic `0600` permissions.
- 📈 **Local Session Rollout Token & Cost Analytics**:
  - Scans `~/.codex/sessions/**/*.jsonl` with an incremental on-disk mtime cache.
  - Implements subagent replay gating to prevent duplicate token count inflation.
  - Calculates daily token spend (Today, Yesterday, 30 days) and per-model cost breakdowns based on official OpenAI rates.
- 🖥️ **Dual Interface**:
  - **Native GNOME Desktop Window**: Beautiful Libadwaita cards matching system dark/light themes.
  - **Interactive CLI & Status Bar**: Clean ANSI terminal output and Waybar-compatible JSON output (`--json`).

---

## 🚀 Getting Started

### One-command install (no root needed)

```bash
git clone https://github.com/Anrahya/openusage-linux.git
cd openusage-linux
./install.sh
```

The installer:

- installs the `openusage-linux` CLI into an isolated venv (`~/.local/share/openusage/venv`) and symlinks it into `~/.local/bin` — no system packages touched, no pip conflicts
- installs and enables the GNOME Shell top-bar extension
- falls back to `pip --user`, then to a zero-dependency symlink install, if venv/pip are unavailable

Then click the OpenUsage icon in your top bar. You need a logged-in Codex CLI
(`~/.config/codex/auth.json` or `~/.codex/auth.json`) and GNOME Shell 45+.
After an extension update on Wayland, log out and back in so GNOME Shell
reloads the new module.

### Optional: GTK4 desktop window

The top bar and CLI need only Python 3.9+. The `--gui` window additionally needs GTK4/Libadwaita — `install.sh` installs it into the venv automatically (skip with `./install.sh --skip-gui`), or install it system-wide:

| Distro        | Command |
|---------------|---------|
| Fedora        | `sudo dnf install python3-gobject gtk4 libadwaita` |
| Debian/Ubuntu | `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1` |
| Arch          | `sudo pacman -S python-gobject gtk4 libadwaita` |

### Manual install

```bash
pip install -e .                  # add ".[gui]" for the desktop window
gnome-extensions install --force gnome-extension/openusage@anrahya.github.io.shell-extension.zip
gnome-extensions enable openusage@anrahya.github.io
```

---

## 💻 Usage

### 1. Terminal Mode

Run `openusage-linux` or `openusage` directly in your terminal:

```bash
openusage-linux
```

```
◆ CODEX USAGE [Pro 5x] (user@example.com)
────────────────────────────────────────────────────────────────
  Weekly         ███████████░░░░░  68.0%  (resets in 3d 4h)
  Spark Weekly   ░░░░░░░░░░░░░░░░   0.0%  (resets in 6d 23h)
  Rate Limit Resets 0 available
  Extra Usage    $0.00 · 0 credits

  Token & Spend History (Last 30 Days)
  Date         Input      Cached     Output     Total      Est. Cost 
  2026-08-16   32.82M     31.67M     159.0k     32.98M     $28.61    

  Model Breakdown
  • gpt-5.6-sol              32.97M     tokens  ($28.60)
  • codex-auto-review        9.6k       tokens  ($0.01)
────────────────────────────────────────────────────────────────
Refreshed at 23:29:36
```

#### Watch Mode (Live Terminal Dashboard)

```bash
openusage-linux --watch
```

### 2. Desktop Application (GTK4 / Libadwaita)

Launch the native GNOME window:

```bash
openusage-linux --gui
```

### 3. Waybar / Polybar Integration

Add to your `~/.config/waybar/config`:

```jsonc
"custom/openusage": {
    "format": "{}",
    "return-type": "json",
    "interval": 60,
    "exec": "openusage-linux --json"
}
```

### 4. GNOME Shell Extension

Install the packaged extension and reload it after updates:

```bash
gnome-extensions disable openusage@anrahya.github.io 2>/dev/null || true
gnome-extensions install --force gnome-extension/openusage@anrahya.github.io.shell-extension.zip
gnome-extensions enable openusage@anrahya.github.io
```

The zip bundles the CLI (`python/`) so an extensions.gnome.org install works
with system `python3`. A local `openusage-linux` (or `OPENUSAGE_BIN`) is used
when present. Rebuild with `./gnome-extension/pack.sh`.

---

## 🧪 Running Tests

```bash
python3 -m unittest discover -s tests
```

---

## 🤝 Contributing

This is a community port, and the most valuable contribution right now needs
no code at all: **verifying providers with a real subscription.** Claude is
ported from the upstream macOS app but still needs testing against a live
account — if you have that plan, it takes about five minutes. See
[CONTRIBUTING.md](CONTRIBUTING.md) and the
[provider verification template](.github/ISSUE_TEMPLATE/provider_verification.md).

Provider fixes with fixture tests, bug reports, and documentation are all
welcome. AI agents working in this repo should read [AGENTS.md](AGENTS.md) first.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

This is an independent Linux port of [OpenUsage](https://github.com/robinebers/openusage)
by Robin Ebrechts — provider API behavior is ported from that project's
published documentation and design. OpenUsage and provider names belong to
their respective owners.
