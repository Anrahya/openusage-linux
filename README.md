# OpenUsage for Linux

A native Linux (Fedora / GNOME / Wayland) port of [OpenUsage](https://github.com/robinebers/openusage) for tracking AI subscription quotas, rate limits, reset credits, and token usage.

Built with **Python 3 + GTK4 + Libadwaita** and a fast CLI companion with Waybar status-bar integration.

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

### Prerequisites (Fedora)

On Fedora, GTK4 and Libadwaita PyGObject bindings are already available. If needed, install via `dnf`:

```bash
sudo dnf install python3-gobject gtk4 libadwaita
```

### Installation

Clone the repository and install in editable mode:

```bash
git clone https://github.com/Anrahya/openusage-linux.git
cd openusage-linux
pip install -e .
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

---

## 🧪 Running Tests

```bash
python3 -m unittest discover -s tests
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
