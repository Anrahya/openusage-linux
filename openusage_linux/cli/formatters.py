"""CLI and Status Bar (Waybar) output formatters."""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import List, Optional

from openusage_linux.core.base import (
    MetricFormat,
    MetricLine,
    ProviderSnapshot,
)


class AnsiColors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    BG_DARK = "\033[48;5;236m"


def format_countdown(resets_at: Optional[datetime]) -> str:
    if not resets_at:
        return ""
    now = datetime.now(timezone.utc)
    diff = resets_at - now
    total_sec = int(diff.total_seconds())
    if total_sec <= 0:
        return "resets now"
    
    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    minutes = (total_sec % 3600) // 60

    if days > 0:
        return f"resets in {days}d {hours}h"
    elif hours > 0:
        return f"resets in {hours}h {minutes}m"
    else:
        return f"resets in {minutes}m"


def format_progress_bar(percentage: float, width: int = 20) -> str:
    clamped = max(0.0, min(100.0, percentage))
    filled_len = int(round(width * clamped / 100.0))
    empty_len = width - filled_len

    if clamped >= 90.0:
        color = AnsiColors.RED
    elif clamped >= 75.0:
        color = AnsiColors.YELLOW
    else:
        color = AnsiColors.GREEN

    bar = f"{color}{'█' * filled_len}{AnsiColors.DIM}{'░' * empty_len}{AnsiColors.RESET}"
    return bar


def format_token_count(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def render_terminal_card(snapshot: ProviderSnapshot) -> str:
    lines: List[str] = []
    w = 64
    sep = "─" * w

    # Header
    title = f"{AnsiColors.BOLD}{AnsiColors.CYAN}◆ {snapshot.provider.display_name.upper()} USAGE{AnsiColors.RESET}"
    plan_pill = f" {AnsiColors.BOLD}[{snapshot.plan}]{AnsiColors.RESET}" if snapshot.plan else ""
    email_text = f"{AnsiColors.DIM}({snapshot.account_email}){AnsiColors.RESET}" if snapshot.account_email else ""
    lines.append(f"\n{title}{plan_pill} {email_text}")
    lines.append(f"{AnsiColors.DIM}{sep}{AnsiColors.RESET}")

    if snapshot.is_error:
        lines.append(f"{AnsiColors.RED}❌ Error: {snapshot.error}{AnsiColors.RESET}")
        lines.append(f"{AnsiColors.DIM}{sep}{AnsiColors.RESET}\n")
        return "\n".join(lines)

    # Metric Lines (Rate limits, resets, credits)
    for ml in snapshot.lines:
        if ml.kind == "progress":
            used_pct = ml.used if ml.used is not None else 0.0
            pbar = format_progress_bar(used_pct, width=16)
            countdown = format_countdown(ml.resets_at)
            cd_fmt = f"{AnsiColors.DIM}({countdown}){AnsiColors.RESET}" if countdown else ""
            
            # Color pct
            if used_pct >= 90.0:
                pct_col = AnsiColors.RED
            elif used_pct >= 75.0:
                pct_col = AnsiColors.YELLOW
            else:
                pct_col = AnsiColors.GREEN

            lines.append(
                f"  {AnsiColors.BOLD}{ml.label:<14}{AnsiColors.RESET} {pbar} {pct_col}{used_pct:5.1f}%{AnsiColors.RESET}  {cd_fmt}"
            )
        elif ml.kind == "values":
            val_strs = []
            for v in ml.values:
                if v.kind == MetricFormat.DOLLARS:
                    val_strs.append(f"{AnsiColors.BOLD}${v.number:.2f}{AnsiColors.RESET}")
                elif v.kind == MetricFormat.COUNT:
                    lbl = f" {v.label}" if v.label else ""
                    val_strs.append(f"{int(v.number)}{lbl}")
            joined_vals = " · ".join(val_strs)
            lines.append(f"  {AnsiColors.BOLD}{ml.label:<14}{AnsiColors.RESET} {joined_vals}")

    # Spend & Usage History
    if snapshot.usage_history and snapshot.usage_history.series:
        lines.append(f"\n  {AnsiColors.BOLD}Token & Spend History (Last 30 Days){AnsiColors.RESET}")
        lines.append(f"  {AnsiColors.DIM}{'Date':<12} {'Input':<10} {'Cached':<10} {'Output':<10} {'Total':<10} {'Est. Cost':<10}{AnsiColors.RESET}")
        
        # Show recent 5 days
        for s in snapshot.usage_history.series[-5:]:
            lines.append(
                f"  {s.date:<12} "
                f"{format_token_count(s.input_tokens):<10} "
                f"{format_token_count(s.cached_tokens):<10} "
                f"{format_token_count(s.output_tokens):<10} "
                f"{format_token_count(s.total_tokens):<10} "
                f"${s.estimated_cost:<9.2f}"
            )

        # Model breakdown
        if snapshot.usage_history.model_usage:
            lines.append(f"\n  {AnsiColors.BOLD}Model Breakdown{AnsiColors.RESET}")
            for m in snapshot.usage_history.model_usage[:4]:
                lines.append(
                    f"  • {AnsiColors.CYAN}{m.model:<24}{AnsiColors.RESET} "
                    f"{format_token_count(m.total_tokens):<10} tokens  "
                    f"(${m.estimated_cost:.2f})"
                )

    refreshed_str = snapshot.refreshed_at.strftime("%H:%M:%S")
    lines.append(f"{AnsiColors.DIM}{sep}{AnsiColors.RESET}")
    lines.append(f"{AnsiColors.DIM}Refreshed at {refreshed_str}{AnsiColors.RESET}\n")

    return "\n".join(lines)


def render_waybar_json(snapshot: ProviderSnapshot) -> str:
    """Renders JSON structure compatible with Waybar custom modules."""
    if snapshot.is_error:
        return json.dumps({
            "text": "Codex: Err",
            "tooltip": f"Error: {snapshot.error}",
            "class": "error",
            "percentage": 0,
        })

    # Find highest used rate limit (e.g. Weekly or Session)
    primary_pct = 0.0
    primary_label = "Codex"
    resets_str = ""

    for ml in snapshot.lines:
        if ml.kind == "progress" and ml.used is not None:
            if ml.used >= primary_pct:
                primary_pct = ml.used
                primary_label = f"Codex {ml.label}"
                resets_str = format_countdown(ml.resets_at)

    tooltip_lines = [f"{snapshot.provider.display_name} ({snapshot.plan or 'Account'})"]
    if snapshot.account_email:
        tooltip_lines.append(f"Account: {snapshot.account_email}")
    tooltip_lines.append("─────────────────────────")

    for ml in snapshot.lines:
        if ml.kind == "progress":
            cd = format_countdown(ml.resets_at)
            cd_text = f" ({cd})" if cd else ""
            tooltip_lines.append(f"{ml.label}: {ml.used:.1f}%{cd_text}")
        elif ml.kind == "values":
            vals = []
            for v in ml.values:
                if v.kind == MetricFormat.DOLLARS:
                    vals.append(f"${v.number:.2f}")
                else:
                    lbl = f" {v.label}" if v.label else ""
                    vals.append(f"{int(v.number)}{lbl}")
            tooltip_lines.append(f"{ml.label}: {' · '.join(vals)}")

    if snapshot.usage_history and snapshot.usage_history.series:
        today_s = snapshot.usage_history.series[-1]
        tooltip_lines.append("─────────────────────────")
        tooltip_lines.append(f"Today: {format_token_count(today_s.total_tokens)} tokens (${today_s.estimated_cost:.2f})")

    css_class = "normal"
    if primary_pct >= 90.0:
        css_class = "critical"
    elif primary_pct >= 75.0:
        css_class = "warning"

    return json.dumps({
        "text": f"{primary_label}: {primary_pct:.0f}%",
        "alt": f"{primary_pct:.0f}%",
        "tooltip": "\n".join(tooltip_lines),
        "class": css_class,
        "percentage": int(primary_pct),
    })
