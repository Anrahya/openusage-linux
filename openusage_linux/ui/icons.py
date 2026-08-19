"""Provider icon lookup for the GTK window."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

ICON_DIR = Path(__file__).resolve().parents[1] / "data" / "icons"

PROVIDER_COLORS = {
    "claude": "#DE7356",
    "codex": "#10A37F",
    "cursor": {"light": "#13120A", "dark": "#F5F5F7"},
    "opencode": {"light": "#6E6E73", "dark": "#AEAEB2"},
    "grok": {"light": "#8E8E93", "dark": "#98989D"},
    "openrouter": "#6467F2",
    "copilot": "#A855F7",
}
FALLBACK_HUES = ("#34C759", "#5856D6", "#FF2D55", "#A2845E")


def provider_icon_path(provider_id: str) -> Optional[Path]:
    if not provider_id:
        return None
    path = ICON_DIR / f"{provider_id}.svg"
    return path if path.exists() else None


def color_for_provider(provider_id: str, dark: bool = False) -> str:
    color = PROVIDER_COLORS.get(provider_id.lower())
    if isinstance(color, dict):
        return color["dark"] if dark else color["light"]
    if isinstance(color, str):
        return color
    digest = 0
    for char in provider_id.lower():
        digest = (digest * 31 + ord(char)) & 0xFFFF
    return FALLBACK_HUES[digest % len(FALLBACK_HUES)]
