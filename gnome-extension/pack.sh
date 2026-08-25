#!/usr/bin/env bash
# Build the EGO zip: GJS sources + SVGs + the stdlib CLI (no GTK window).
# Usage: ./gnome-extension/pack.sh [outfile.zip]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="$ROOT/gnome-extension"
UUID="openusage@anrahya.github.io"
OUT="${1:-$EXT/$UUID.shell-extension.zip}"

have() { command -v "$1" >/dev/null 2>&1; }
have zip || { echo "zip is required to pack the GNOME extension" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp "$EXT/metadata.json" "$EXT/extension.js" "$EXT/stylesheet.css" "$STAGE/"
cp "$EXT/"*.svg "$STAGE/"

mkdir -p "$STAGE/python"
while IFS= read -r -d '' src; do
    rel="${src#"$ROOT/"}"
    dest="$STAGE/python/$rel"
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
done < <(find "$ROOT/openusage_linux" -type f \
    ! -path '*/ui/*' \
    ! -path '*/__pycache__/*' \
    ! -path '*/data/icons/*' \
    ! -name '*.pyc' \
    \( -name '*.py' -o -name '*.json' \) \
    -print0)

rm -f "$OUT"
(
    cd "$STAGE"
    shopt -s nullglob
    zip -q -r "$OUT" metadata.json extension.js stylesheet.css python *.svg
)

echo "Packed $OUT"
unzip -l "$OUT" | awk 'NR<=12 || /files$/'
