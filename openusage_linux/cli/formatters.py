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
from openusage_linux.core.settings import load_prefs, public_prefs


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


def format_countdown(resets_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if not resets_at:
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    diff = resets_at - now
    total_sec = int(diff.total_seconds())
    if total_sec <= 0:
        return "resets now"

    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    minutes = (total_sec % 3600) // 60

    if days > 0:
        return f"resets in {days}d {hours}h"
    if hours > 0:
        return f"resets in {hours}h {minutes}m"
    if minutes > 0:
        return f"resets in {minutes}m"
    return "resets now"


def format_progress_bar(percentage: float, width: int = 20) -> str:
    clamped = max(0.0, min(100.0, percentage))
    filled_len = int(width * clamped / 100.0)
    if 0.0 < clamped < 100.0 and filled_len == 0:
        filled_len = 1
    if clamped < 100.0 and filled_len == width:
        filled_len = width - 1
    empty_len = width - filled_len

    if clamped >= 90.0:
        color = AnsiColors.RED
    elif clamped >= 80.0:
        color = AnsiColors.YELLOW
    else:
        color = AnsiColors.GREEN

    return f"{color}{'█' * filled_len}{AnsiColors.DIM}{'░' * empty_len}{AnsiColors.RESET}"


def format_token_count(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def _format_metric_value(v) -> str:
    if v.kind == MetricFormat.DOLLARS:
        return f"{AnsiColors.BOLD}${v.number:.2f}{AnsiColors.RESET}"
    if v.kind == MetricFormat.TOKENS:
        return format_token_count(int(v.number))
    label = f" {v.label}" if v.label else ""
    return f"{int(v.number)}{label}"


def _render_snapshot_block(snapshot: ProviderSnapshot) -> List[str]:
    lines: List[str] = []
    w = 64
    sep = "─" * w

    title = f"{AnsiColors.BOLD}{AnsiColors.CYAN}◆ {snapshot.provider.display_name.upper()} USAGE{AnsiColors.RESET}"
    plan_pill = f" {AnsiColors.BOLD}[{snapshot.plan}]{AnsiColors.RESET}" if snapshot.plan else ""
    email_text = f" {AnsiColors.DIM}({snapshot.account_email}){AnsiColors.RESET}" if snapshot.account_email else ""
    lines.append(f"\n{title}{plan_pill}{email_text}")
    lines.append(f"{AnsiColors.DIM}{sep}{AnsiColors.RESET}")

    if snapshot.is_error:
        lines.append(f"{AnsiColors.RED}❌ Error: {snapshot.error}{AnsiColors.RESET}")
        lines.append(f"{AnsiColors.DIM}{sep}{AnsiColors.RESET}")
        return lines

    for ml in snapshot.lines:
        if ml.kind == "progress":
            used_pct = ml.used if ml.used is not None else 0.0
            if ml.format == MetricFormat.PERCENT:
                pbar = format_progress_bar(used_pct, width=16)
                reading = f"{used_pct:5.1f}%"
                if used_pct >= 90.0:
                    reading_col = AnsiColors.RED
                elif used_pct >= 80.0:
                    reading_col = AnsiColors.YELLOW
                else:
                    reading_col = AnsiColors.GREEN
            else:
                limit = ml.limit or 0.0
                fraction = (used_pct / limit) if limit > 0 else 0.0
                pbar = format_progress_bar(fraction * 100.0, width=16)
                if ml.format == MetricFormat.DOLLARS:
                    reading = f"${used_pct:.2f} / ${limit:.2f}"
                elif ml.format == MetricFormat.COUNT:
                    reading = f"{int(used_pct)} / {int(limit)}"
                else:
                    reading = f"{used_pct:.0f} / {limit:.0f}"
                reading_col = AnsiColors.BOLD

            countdown = format_countdown(ml.resets_at)
            cd_fmt = f"  {AnsiColors.DIM}({countdown}){AnsiColors.RESET}" if countdown else ""
            lines.append(
                f"  {AnsiColors.BOLD}{ml.label:<16}{AnsiColors.RESET} {pbar} "
                f"{reading_col}{reading}{AnsiColors.RESET}{cd_fmt}"
            )
        elif ml.kind == "values":
            val_strs = [_format_metric_value(v) for v in ml.values]
            joined = " · ".join(val_strs)
            lines.append(f"  {AnsiColors.BOLD}{ml.label:<16}{AnsiColors.RESET} {joined}")
        elif ml.kind in ("no_data", "badge"):
            note = ml.note or "No data available"
            lines.append(f"  {AnsiColors.BOLD}{ml.label:<16}{AnsiColors.RESET} {AnsiColors.DIM}{note}{AnsiColors.RESET}")

    if snapshot.usage_history and snapshot.usage_history.series:
        lines.append(f"\n  {AnsiColors.BOLD}Token & Spend History (Last 30 Days){AnsiColors.RESET}")
        lines.append(
            f"  {AnsiColors.DIM}{'Date':<12} {'Input':<10} {'Cached':<10} {'Output':<10} "
            f"{'Total':<10} {'Est. Cost':<10}{AnsiColors.RESET}"
        )
        for s in snapshot.usage_history.series[-7:]:
            lines.append(
                f"  {s.date:<12} "
                f"{format_token_count(s.input_tokens):<10} "
                f"{format_token_count(s.cached_tokens):<10} "
                f"{format_token_count(s.output_tokens):<10} "
                f"{format_token_count(s.total_tokens):<10} "
                f"${s.estimated_cost:<9.2f}"
            )

        if snapshot.usage_history.model_usage:
            lines.append(f"\n  {AnsiColors.BOLD}Model Breakdown{AnsiColors.RESET}")
            for m in snapshot.usage_history.model_usage[:4]:
                lines.append(
                    f"  • {AnsiColors.CYAN}{m.model:<24}{AnsiColors.RESET} "
                    f"{format_token_count(m.total_tokens):<10} tokens  "
                    f"(${m.estimated_cost:.2f})"
                )

    lines.append(f"{AnsiColors.DIM}{sep}{AnsiColors.RESET}")
    return lines


def render_terminal_card(snapshots) -> str:
    """Render one or more provider snapshots as stacked terminal cards."""
    if isinstance(snapshots, ProviderSnapshot):
        snapshots = [snapshots]
    lines: List[str] = []
    for snapshot in snapshots:
        lines.extend(_render_snapshot_block(snapshot))
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    refreshed = max((_aware(s.refreshed_at) for s in snapshots), default=datetime.now(timezone.utc))
    lines.append(f"{AnsiColors.DIM}Refreshed at {refreshed.astimezone().strftime('%H:%M:%S')}{AnsiColors.RESET}\n")
    return "\n".join(lines)


def snapshot_to_dict(snapshot: ProviderSnapshot) -> Dict[str, Any]:
    """Serializes a snapshot into a comprehensive dictionary for extensions and status bars."""
    if snapshot.is_error:
        return {
            "provider": {"id": snapshot.provider.id, "display_name": snapshot.provider.display_name},
            "is_error": True,
            "error": snapshot.error,
            "text": f"{snapshot.provider.display_name}: Err",
            "tooltip": f"Error: {snapshot.error}",
            "class": "critical",
            "percentage": 0,
        }

    rate_limits = []
    primary_pct = 0.0
    primary_label = ""
    resets_str = ""

    for ml in snapshot.lines:
        if ml.kind == "progress" and ml.used is not None:
            if ml.format == MetricFormat.PERCENT:
                pct = ml.used
            elif ml.limit:
                pct = max(0.0, min(100.0, ml.used / ml.limit * 100.0))
            else:
                pct = 0.0
            cd = format_countdown(ml.resets_at)
            status_class = "critical" if pct >= 90.0 else ("warning" if pct >= 80.0 else "normal")

            rate_limits.append({
                "label": ml.label,
                "used": ml.used,
                "limit": ml.limit,
                "format": ml.format.value,
                "percentage": pct,
                "resets_in": cd,
                "resets_at": ml.resets_at.isoformat() if ml.resets_at else None,
                "period_seconds": int(ml.period_duration_ms / 1000) if ml.period_duration_ms else None,
                "class": status_class,
            })

            if pct > primary_pct:
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
        today_s = snapshot.usage_history.entry_for_date()
        if today_s:
            spend_data["today_tokens"] = today_s.total_tokens
            spend_data["today_cost"] = today_s.estimated_cost
            spend_data["today_input"] = today_s.input_tokens
            spend_data["today_cached"] = today_s.cached_tokens
            spend_data["today_output"] = today_s.output_tokens

            if today_s.input_tokens > 0:
                spend_data["cache_hit_rate"] = round((today_s.cached_tokens / today_s.input_tokens) * 100.0, 1)

        spend_data["total_tokens_30d"] = sum(s.total_tokens for s in snapshot.usage_history.series)
        spend_data["total_cost_30d"] = sum(s.estimated_cost for s in snapshot.usage_history.series)

        for s in snapshot.usage_history.series:
            spend_data["daily_series"].append({
                "date": s.date,
                "tokens": s.total_tokens,
                "cost": s.estimated_cost,
                "input": s.input_tokens,
                "cached": s.cached_tokens,
                "output": s.output_tokens,
                "models": [
                    {
                        "model": model.model,
                        "tokens": model.total_tokens,
                        "cost": model.estimated_cost,
                        "input": model.input_tokens,
                        "cached": model.cached_tokens,
                        "output": model.output_tokens,
                    }
                    for model in s.models
                ],
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

    display_name = snapshot.provider.display_name
    tooltip_lines = [f"{display_name} ({snapshot.plan or 'Account'})"]
    if snapshot.account_email:
        tooltip_lines.append(f"Account: {snapshot.account_email}")
    tooltip_lines.append("─────────────────────────")
    for rl in rate_limits:
        cd_t = f" ({rl['resets_in']})" if rl["resets_in"] else ""
        tooltip_lines.append(f"{rl['label']}: {rl['percentage']:.1f}%{cd_t}")
    if spend_data["today_tokens"] > 0:
        tooltip_lines.append("─────────────────────────")
        tooltip_lines.append(f"Today: {format_token_count(spend_data['today_tokens'])} tokens (${spend_data['today_cost']:.2f})")

    css_class = "critical" if primary_pct >= 90.0 else ("warning" if primary_pct >= 80.0 else "normal")

    return {
        "provider": {"id": snapshot.provider.id, "display_name": display_name},
        "plan": snapshot.plan,
        "account_email": snapshot.account_email,
        "primary_metric": {
            "label": f"{display_name} {primary_label}".strip(),
            "percentage": primary_pct,
            "resets_in": resets_str,
            "class": css_class,
        },
        "rate_limits": rate_limits,
        "credits": credits_data,
        "spend_history": spend_data,
        "refreshed_at": snapshot.refreshed_at.strftime("%H:%M:%S"),
        "is_error": False,
        "text": f"{display_name} {primary_label}: {primary_pct:.0f}%".strip(),
        "alt": f"{primary_pct:.0f}%",
        "tooltip": "\n".join(tooltip_lines),
        "class": css_class,
        "percentage": int(primary_pct),
    }


def render_waybar_json(
    snapshots,
    available_providers: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
    prefs: Optional[Dict[str, Any]] = None,
) -> str:
    """Multi-provider JSON: full per-provider data + top-level Waybar fields
    driven by the most-constrained provider."""
    if isinstance(snapshots, ProviderSnapshot):
        snapshots = [snapshots]

    provider_dicts = [snapshot_to_dict(s) for s in snapshots]
    healthy = [d for d in provider_dicts if not d.get("is_error")]
    available = available_providers if available_providers is not None else [
        {"id": d["provider"]["id"], "display_name": d["provider"]["display_name"], "enabled": True}
        for d in provider_dicts if not d.get("is_error")
    ]
    enabled = [p["id"] for p in available if p.get("enabled")]
    ui_prefs = public_prefs(prefs if prefs is not None else load_prefs())

    if not healthy:
        first_error = error or next(
            (d.get("error") for d in provider_dicts if d.get("error")),
            "No providers available",
        )
        return json.dumps({
            "providers": provider_dicts,
            "available_providers": available,
            "enabled_providers": enabled,
            "prefs": ui_prefs,
            "is_error": True,
            "error": first_error,
            "text": "OpenUsage: Err",
            "tooltip": f"Error: {first_error}",
            "class": "critical",
            "percentage": 0,
        })

    primary = max(healthy, key=lambda d: d.get("percentage", 0))
    tooltip_blocks = [d["tooltip"] for d in healthy]

    return json.dumps({
        "providers": provider_dicts,
        "available_providers": available,
        "enabled_providers": enabled,
        "prefs": ui_prefs,
        "provider": primary["provider"],
        "plan": primary.get("plan"),
        "primary_metric": primary.get("primary_metric"),
        "rate_limits": primary.get("rate_limits"),
        "credits": primary.get("credits"),
        "spend_history": primary.get("spend_history"),
        "refreshed_at": primary.get("refreshed_at"),
        "is_error": False,
        "text": primary.get("text"),
        "alt": primary.get("alt"),
        "tooltip": "\n\n".join(tooltip_blocks),
        "class": primary.get("class"),
        "percentage": primary.get("percentage"),
    })
