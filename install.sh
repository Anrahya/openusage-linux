#!/usr/bin/env bash
# OpenUsage for Linux — one-command installer.
#
# Installs the `openusage-linux` CLI and the GNOME Shell top-bar extension.
# Requires only bash, Python 3.9+, and GNOME Shell 45+. No root needed.
#
# Usage:  ./install.sh
#         ./install.sh --skip-gui   (skip GTK dependencies; top bar + CLI only)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID="openusage@anrahya.github.io"
ZIP="$REPO_ROOT/gnome-extension/$UUID.shell-extension.zip"
VENV_DIR="$HOME/.local/share/openusage/venv"
BIN_DIR="$HOME/.local/bin"
WITH_GUI=1

for arg in "$@"; do
    case "$arg" in
        --skip-gui) WITH_GUI=0 ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

have() { command -v "$1" >/dev/null 2>&1; }

log()  { printf '\033[1;32m✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✘\033[0m %s\n' "$*" >&2; exit 1; }

echo "OpenUsage for Linux installer"
echo "──────────────────────────────────────────────"

# ── 1. Sanity checks ────────────────────────────────────────────────
PYTHON=python3
if ! have "$PYTHON"; then
    die "python3 not found. Install it first:
  Fedora:        sudo dnf install python3
  Debian/Ubuntu: sudo apt install python3
  Arch:          sudo pacman -S python"
fi

case "$("$PYTHON" -c 'import sys; print(sys.version_info >= (3, 9))')" in
    True) ;;
    *) die "Python 3.9+ required (found: $("$PYTHON" --version 2>&1))" ;;
esac
log "Python: $("$PYTHON" --version 2>&1)"

desktop="${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-}}"
case "$desktop" in
    *GNOME*) ;;
    *)
        warn "This does not look like a GNOME session (desktop='${desktop:-unset}')."
        warn "The top-bar extension needs GNOME Shell 45+. Continuing anyway."
        ;;
esac

# ── 2. Install the CLI ──────────────────────────────────────────────
install_venv() {
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null || return 1
    [ -x "$VENV_DIR/bin/python3" ] || return 1
    if [ ! -x "$VENV_DIR/bin/pip" ]; then
        "$VENV_DIR/bin/python3" -m ensurepip >/dev/null 2>&1 || return 1
    fi
    if [ "$WITH_GUI" -eq 1 ]; then
        "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT[gui]" 2>/dev/null || \
        "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT" || return 1
    else
        "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT" || return 1
    fi
    return 0
}

install_user_pip() {
    local extra=""
    [ "$WITH_GUI" -eq 1 ] && extra="[gui]"
    "$PYTHON" -m pip install --quiet --user -e "$REPO_ROOT$extra" 2>/dev/null || \
    "$PYTHON" -m pip install --quiet --user --break-system-packages -e "$REPO_ROOT$extra"
}

install_symlink() {
    # Zero-dependency fallback: the repo wrapper script needs only Python.
    mkdir -p "$BIN_DIR"
    ln -sf "$REPO_ROOT/bin/openusage-linux" "$BIN_DIR/openusage-linux"
    ln -sf "$REPO_ROOT/bin/openusage-linux" "$BIN_DIR/openusage"
}

if install_venv; then
    mkdir -p "$BIN_DIR"
    ln -sf "$VENV_DIR/bin/openusage-linux" "$BIN_DIR/openusage-linux"
    ln -sf "$VENV_DIR/bin/openusage" "$BIN_DIR/openusage"
    log "CLI installed in isolated venv: $VENV_DIR"
elif install_user_pip; then
    log "CLI installed via pip --user"
else
    warn "pip unavailable — falling back to a symlink install (no pip needed)."
    install_symlink
    log "CLI linked from repo: $REPO_ROOT/bin"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in your PATH. Add this to your shell profile:
    export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ── 3. Install + enable the GNOME Shell extension ───────────────────
if have gnome-extensions; then
    [ -f "$ZIP" ] || die "Extension package not found: $ZIP"
    gnome-extensions disable "$UUID" 2>/dev/null || true
    gnome-extensions install --force "$ZIP"
    gnome-extensions enable "$UUID"
    log "GNOME Shell extension installed and enabled ($UUID)"
else
    warn "gnome-extensions command not found — skipping top-bar extension."
    warn "On GNOME 45+ you can install it manually:
    gnome-extensions install --force $ZIP"
fi

# ── 4. Done ─────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────"
echo "Installed. Click the OpenUsage icon in your top bar."
echo
echo "Notes:"
echo "  • First launch fetches your Codex usage (~10s)."
echo "  • Requires a logged-in Codex CLI (~/.codex/auth.json)."
echo "  • Extension updates need one logout/login (Shell caches code)."
echo "  • Try the terminal version:  openusage-linux"
