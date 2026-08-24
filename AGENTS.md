# AGENTS.md — instructions for AI agents

OpenUsage for Linux tracks AI subscription quotas, rate limits, and token spend
in a GNOME top-bar extension, a terminal CLI, and an optional GTK4 window.

Two jobs show up in this repo. Do the one the user asked for:

1. **Install it on their machine** → [Install this for the user](#install-this-for-the-user)
2. **Change the code** → [Work on this repo](#work-on-this-repo)

Never ask the user to paste API keys, cookies, or tokens. Credentials are
whatever Codex / Claude Code / Cursor / OpenCode / `grok login` already wrote
to disk.

---

## Install this for the user

Follow this playbook. Do not invent a different installer, do not `sudo pip`,
and do not enable the GNOME extension on a non-GNOME desktop.

### 1. Detect the machine

```bash
python3 -c 'import sys; print(sys.version)'
echo "desktop=${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-unset}}"
gnome-shell --version 2>/dev/null || true
echo "PATH=$PATH"
```

Need **Python 3.9+**. If `python3` is missing, tell them to install it (do not
guess a package manager unless you can see the distro):

- Fedora: `sudo dnf install python3`
- Debian/Ubuntu: `sudo apt install python3`
- Arch: `sudo pacman -S python`

The **top-bar icon** needs GNOME Shell **45–50**. The **CLI** works on any
desktop (KDE, Hyprland, Sway, COSMIC, …). `--json` is for Waybar/Polybar.

### 2. Get the code and install

If you are not already in a clone:

```bash
git clone https://github.com/Anrahya/openusage-linux.git
cd openusage-linux
```

Then:

```bash
./install.sh                 # CLI + GNOME extension when gnome-extensions exists
# ./install.sh --skip-gui    # CLI + extension, skip GTK4 window deps
```

`install.sh` is rootless: venv at `~/.local/share/openusage/venv`, shims in
`~/.local/bin`. It turns GNOME's `disable-user-extensions` kill switch off if
it was on.

If `~/.local/bin` is not on `PATH`, add it to their shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 3. Verify

```bash
openusage-linux --list
openusage-linux
```

`--list` prints which providers were detected from local logins. An empty list
means they are not logged into Codex, Claude Code, Cursor, OpenCode, or Grok —
tell them to sign in with that tool. Do **not** scrape or print token files.

First fetch can take ~10s (provider APIs).

### 4. Desktop-specific wrap-up

**GNOME 45+ (Wayland):** the icon should appear in the top bar. If it does not:

```bash
gsettings get org.gnome.shell disable-user-extensions
gnome-extensions info openusage@anrahya.github.io
```

If the extension is enabled but stale, they must **log out and back in** —
GNOME Shell caches extension modules. Do not tell them to `Alt+F2 r` on Wayland.

**Hyprland / Sway / Waybar:** skip worrying about the top bar. Add:

```jsonc
"custom/openusage": {
    "format": "{}",
    "return-type": "json",
    "interval": 60,
    "exec": "openusage-linux --json"
}
```

**Optional GTK window** (needs GTK4/Libadwaita): `openusage-linux --gui`.

### 5. Stop

Setup is done. Do not start porting providers, bumping the extension zip, or
opening PRs unless they asked.

---

## Work on this repo

```bash
./install.sh
python3 -m unittest discover -s tests    # offline, no network needed
openusage-linux
openusage-linux --json
```

### Repo map

- `openusage_linux/core/providers/<id>/` — one package per provider, always
  three parts: `auth.py` (reads credentials the provider's own CLI left on
  disk), `client.py` (HTTP), `mapper.py` (payload → `MetricLine`). Claude, Grok,
  and OpenCode also have a local `scanner.py` for session-log spend history.
- `openusage_linux/core/base.py` — the shared data model (`ProviderSnapshot`,
  `MetricLine`). Every UI renders from this; don't bypass it.
- `openusage_linux/cli/` — `main.py` iterates the registry, `formatters.py`
  owns terminal + JSON output.
- `openusage_linux/ui/` — optional GTK4/Libadwaita window (`--gui`).
- `gnome-extension/` — GJS top-bar extension. Ships as a **built zip**; after
  editing `extension.js`/`stylesheet.css` you MUST bump `metadata.json`
  version and rebuild:
  `zip openusage@anrahya.github.io.shell-extension.zip metadata.json extension.js stylesheet.css openusage.svg codex.svg claude.svg cursor.svg opencode.svg grok.svg`
- `tests/` — unittest, fixture-driven, offline.

### The provider porting blueprint

Providers are ported from the upstream macOS app (github.com/robinebers/openusage —
not vendored in this public repo). Its Swift sources define the exact
endpoints, headers, payload shapes, and auth flows; mirror them, don't guess.
Never ask users to paste tokens — read credentials the provider's own tool
already wrote to disk.

### Gotchas that have already bitten us

- **GNOME Shell is not GTK.** `St.Widget`/`St.Button` have no
  `set_tooltip_text` and friends — those are GTK APIs and crash the shell
  extension at runtime. Use `accessible_name` property assignment.
- **Extensions are module-cached.** After reinstalling, the running shell
  keeps the old code until the user logs out/in (Wayland). Tests of
  extension changes need a session restart.
- **Meter geometry is fixed-width.** The popover is 320px; meter troughs are
  264px (`METER_WIDTH` in extension.js). Fill widths and pace-tick offsets
  are computed from that constant.
- **JSON contract:** `render_waybar_json` emits a `providers` array plus
  top-level Waybar fields from the most-constrained provider, and a `prefs`
  object (`period`, `metric`, `refresh_interval`, `show_total_spend`). Keep
  both stable — status bars and the extension depend on them.
- Rate-limit severity bands follow the macOS app: warning ≥ 80%, critical ≥ 90%.
- Codex auth writes must stay atomic with `0600` permissions.

### Verification reality

Codex, Cursor, and OpenCode are verified against live subscriptions. Claude
is a faithful port awaiting verification by users who hold that
subscription — see CONTRIBUTING.md for the verification workflow. Keep
parsing defensive: unknown payloads should render "No data", never crash.
