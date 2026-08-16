"""CLI, Status Bar (Waybar), and GNOME Shell Extension output formatters."""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


def snapshot_to_dict(snapshot: ProviderSnapshot) -> Dict[str, Any]:
    """Serializes a snapshot into a comprehensive dictionary for extensions and status bars."""
    if snapshot.is_error:
        return {
            "provider": {"id": snapshot.provider.id, "display_name": snapshot.provider.display_name},
            "is_error": True,
            "error": snapshot.error,
            "text": "Codex: Err",
            "tooltip": f"Error: {snapshot.error}",
            "class": "critical",
            "percentage": 0,
        }

    rate_limits = []
    primary_pct = 0.0
    primary_label = "Weekly"
    resets_str = ""

    for ml in snapshot.lines:
        if ml.kind == "progress" and ml.used is not None:
            pct = ml.used
            cd = format_countdown(ml.resets_at)
            status_class = "critical" if pct >= 90.0 else ("warning" if pct >= 75.0 else "normal")
            
            rate_limits.append({
                "label": ml.label,
                "used": pct,
                "limit": 100.0,
                "resets_in": cd,
                "resets_at": ml.resets_at.isoformat() if ml.resets_at else None,
                "class": status_class,
            })

            if pct >= primary_pct:
                primary_pct = pct
                primary_label = ml.label
                resets_str = cd

    credits_data = {
        "rate_limit_resets": 0,
        "extra_usage_dollars": 0.0,
        "extra_usage_credits": 0,
    }

    for ml in snapshot.lines:
        if ml.kind == "values":
            if ml.label == "Rate Limit Resets":
                for v in ml.values:
                    if v.kind == MetricFormat.COUNT:
                        credits_data["rate_limit_resets"] = int(v.number)
            elif ml.label == "Extra Usage":
                for v in ml.values:
                    if v.kind == MetricFormat.DOLLARS:
                        credits_data["extra_usage_dollars"] = v.number
                    elif v.kind == MetricFormat.COUNT:
                        credits_data["extra_usage_credits"] = int(v.number)

    spend_data = {
        "today_tokens": 0,
        "today_cost": 0.0,
        "today_input": 0,
        "today_cached": 0,
        "today_output": 0,
        "cache_hit_rate": 0.0,
        "total_tokens_30d": 0,
        "total_cost_30d": 0.0,
        "models": [],
        "daily_series": [],
    }

    if snapshot.usage_history and snapshot.usage_history.series:
        today_s = snapshot.usage_history.series[-1]
        spend_data["today_tokens"] = today_s.total_tokens
        spend_data["today_cost"] = today_s.estimated_cost
        spend_data["today_input"] = today_s.input_tokens
        spend_data["today_cached"] = today_s.cached_tokens
        spend_data["today_output"] = today_s.output_tokens
        
        if today_s.input_tokens > 0:
            spend_data["cache_hit_rate"] = round((today_s.cached_tokens / today_s.input_tokens) * 100.0, 1)

        spend_data["total_tokens_30d"] = sum(s.total_tokens for s in snapshot.usage_history.series)
        spend_data["total_cost_30d"] = sum(s.estimated_cost for s in snapshot.usage_history.series)

        for s in snapshot.usage_history.series[-7:]:
            spend_data["daily_series"].append({
                "date": s.date,
                "tokens": s.total_tokens,
                "cost": s.estimated_cost,
                "input": s.input_tokens,
                "cached": s.cached_tokens,
                "output": s.output_tokens,
            })

        for m in snapshot.usage_history.model_usage:
            spend_data["models"].append({
                "model": m.model,
                "tokens": m.total_tokens,
                "cost": m.estimated_cost,
                "input": m.input_tokens,
                "cached": m.cached_tokens,
                "output": m.output_tokens,
            })

    # Tooltip text for simple toolbars
    tooltip_lines = [f"{snapshot.provider.display_name} ({snapshot.plan or 'Account'})"]
    if snapshot.account_email:
        tooltip_lines.append(f"Account: {snapshot.account_email}")
    tooltip_lines.append("─────────────────────────")
    for rl in rate_limits:
        cd_t = f" ({rl['resets_in']})" if rl["resets_in"] else ""
        tooltip_lines.append(f"{rl['label']}: {rl['used']:.1f}%{cd_t}")
    if spend_data["today_tokens"] > 0:
        tooltip_lines.append("─────────────────────────")
        tooltip_lines.append(f"Today: {format_token_count(spend_data['today_tokens'])} tokens (${spend_data['today_cost']:.2f})")

    css_class = "critical" if primary_pct >= 90.0 else ("warning" if primary_pct >= 75.0 else "normal")

    return {
        "provider": {"id": snapshot.provider.id, "display_name": snapshot.provider.display_name},
        "plan": snapshot.plan,
        "account_email": snapshot.account_email,
        "primary_metric": {
            "label": f"Codex {primary_label}",
            "percentage": primary_pct,
            "resets_in": resets_str,
            "class": css_class,
        },
        "rate_limits": rate_limits,
        "credits": credits_data,
        "spend_history": spend_data,
        "refreshed_at": snapshot.refreshed_at.strftime("%H:%M:%S"),
        "is_error": False,
        # Waybar standard fields
        "text": f"Codex {primary_label}: {primary_pct:.0f}%",
        "alt": f"{primary_pct:.0f}%",
        "tooltip": "\n".join(tooltip_lines),
        "class": css_class,
        "percentage": int(primary_pct),
    }


def render_waybar_json(snapshot: ProviderSnapshot) -> str:
    """Renders complete structured JSON."""
    return json.dumps(snapshot_to_dict(snapshot))
