"""Codex Usage Mapper for transforming Wham API payloads into metric lines."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from openusage_linux.core.base import MetricFormat, MetricLine, MetricValue


class CodexUsageMapper:
    CREDIT_USD_RATE = 0.04
    SESSION_PERIOD_SEC = 18000    # 5 hours
    WEEKLY_PERIOD_SEC = 604800    # 7 days

    @classmethod
    def format_plan_name(cls, raw: Optional[str]) -> Optional[str]:
        if not raw or not isinstance(raw, str) or not raw.strip():
            return None
        raw_clean = raw.strip().lower()
        if raw_clean == "prolite":
            return "Pro 5x"
        elif raw_clean == "pro":
            return "Pro 20x"
        elif raw_clean == "team":
            return "Team"
        elif raw_clean == "enterprise":
            return "Enterprise"
        elif raw_clean == "free":
            return "Free"
        return raw.replace("_", " ").title()

    @classmethod
    def map_usage(
        cls,
        body: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        reset_credits_payload: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[Optional[str], List[MetricLine]]:
        now = now or datetime.now(timezone.utc)
        headers = headers or {}
        lines: List[MetricLine] = []

        # 1. Rate Limit Windows (Session / Weekly)
        rate_limit = body.get("rate_limit")
        if isinstance(rate_limit, dict):
            primary = rate_limit.get("primary_window")
            secondary = rate_limit.get("secondary_window")

            header_primary = cls._parse_float(headers.get("x-codex-primary-used-percent"))
            header_secondary = cls._parse_float(headers.get("x-codex-secondary-used-percent"))

            classified = cls._classify_windows(
                primary=primary,
                secondary=secondary,
                header_primary=header_primary,
                header_secondary=header_secondary,
                session_label="Session",
                weekly_label="Weekly",
                now=now,
            )
            lines.extend(classified)

        # 2. Additional Rate Limits (e.g. Spark)
        additional = body.get("additional_rate_limits")
        if isinstance(additional, list):
            for entry in additional:
                if isinstance(entry, dict):
                    name = str(entry.get("limit_name", "")).lower()
                    feat = str(entry.get("metered_feature", "")).lower()
                    if "spark" in name or "spark" in feat:
                        sub_rl = entry.get("rate_limit")
                        if isinstance(sub_rl, dict):
                            spark_lines = cls._classify_windows(
                                primary=sub_rl.get("primary_window"),
                                secondary=sub_rl.get("secondary_window"),
                                session_label="Spark",
                                weekly_label="Spark Weekly",
                                now=now,
                            )
                            lines.extend(spark_lines)

        # 3. Rate Limit Resets
        resets_info = cls._read_reset_credits(body=body, dedicated=reset_credits_payload)
        if resets_info is not None:
            count, expiries = resets_info
            lines.append(
                MetricLine.values_line(
                    label="Rate Limit Resets",
                    values=[MetricValue(number=float(count), kind=MetricFormat.COUNT, label="available")],
                    expiries_at=expiries,
                )
            )

        # 4. Flex Credits / Extra Usage
        credits_rem = cls._read_credits_remaining(body=body, headers=headers)
        if credits_rem is not None:
            count = max(0, int(credits_rem))
            usd_val = count * cls.CREDIT_USD_RATE
            lines.append(
                MetricLine.values_line(
                    label="Extra Usage",
                    values=[
                        MetricValue(number=usd_val, kind=MetricFormat.DOLLARS),
                        MetricValue(number=float(count), kind=MetricFormat.COUNT, label="credits"),
                    ],
                )
            )

        plan = cls.format_plan_name(body.get("plan_type"))
        return plan, lines

    @classmethod
    def _classify_windows(
        cls,
        primary: Optional[Dict[str, Any]],
        secondary: Optional[Dict[str, Any]],
        header_primary: Optional[float] = None,
        header_secondary: Optional[float] = None,
        session_label: str = "Session",
        weekly_label: str = "Weekly",
        now: Optional[datetime] = None,
    ) -> List[MetricLine]:
        now = now or datetime.now(timezone.utc)
        candidates = []

        if primary is not None or header_primary is not None:
            used = cls._parse_float(primary.get("used_percent") if isinstance(primary, dict) else None)
            if used is None:
                used = header_primary
            candidates.append({"window": primary or {}, "used": used, "fallback": "session"})

        if secondary is not None or header_secondary is not None:
            used = cls._parse_float(secondary.get("used_percent") if isinstance(secondary, dict) else None)
            if used is None:
                used = header_secondary
            candidates.append({"window": secondary or {}, "used": used, "fallback": "weekly"})

        session_line: Optional[MetricLine] = None
        weekly_line: Optional[MetricLine] = None

        for c in candidates:
            w = c["window"]
            used = c["used"]
            if used is None:
                continue

            sec = cls._parse_float(w.get("limit_window_seconds"))
            resets_at = cls._parse_reset_date(w, now=now)
            period_ms = int(sec * 1000) if sec is not None else None

            # Classification by window length. Unknown durations still render
            # using the primary/secondary fallback so a new API window is not
            # silently dropped.
            if sec == cls.SESSION_PERIOD_SEC or (sec is None and c["fallback"] == "session"):
                session_line = MetricLine.progress(
                    label=session_label,
                    used=used,
                    limit=100.0,
                    resets_at=resets_at,
                    period_duration_ms=period_ms or (cls.SESSION_PERIOD_SEC * 1000),
                )
            elif sec == cls.WEEKLY_PERIOD_SEC or (sec is None and c["fallback"] == "weekly"):
                weekly_line = MetricLine.progress(
                    label=weekly_label,
                    used=used,
                    limit=100.0,
                    resets_at=resets_at,
                    period_duration_ms=period_ms or (cls.WEEKLY_PERIOD_SEC * 1000),
                )
            elif c["fallback"] == "session" and session_line is None:
                session_line = MetricLine.progress(
                    label=session_label,
                    used=used,
                    limit=100.0,
                    resets_at=resets_at,
                    period_duration_ms=period_ms or (cls.SESSION_PERIOD_SEC * 1000),
                )
            elif weekly_line is None:
                weekly_line = MetricLine.progress(
                    label=weekly_label,
                    used=used,
                    limit=100.0,
                    resets_at=resets_at,
                    period_duration_ms=period_ms or (cls.WEEKLY_PERIOD_SEC * 1000),
                )

        result: List[MetricLine] = []
        if session_line:
            result.append(session_line)
        if weekly_line:
            result.append(weekly_line)
        return result

    @classmethod
    def _parse_reset_date(cls, window: Dict[str, Any], now: datetime) -> Optional[datetime]:
        reset_at = cls._parse_float(window.get("reset_at"))
        if reset_at is not None:
            return datetime.fromtimestamp(reset_at, tz=timezone.utc)

        reset_after = cls._parse_float(window.get("reset_after_seconds"))
        if reset_after is not None:
            return datetime.fromtimestamp(now.timestamp() + reset_after, tz=timezone.utc)
        return None

    @classmethod
    def _read_reset_credits(
        cls, body: Dict[str, Any], dedicated: Optional[Dict[str, Any]] = None
    ) -> Optional[Tuple[int, List[datetime]]]:
        source = dedicated if (dedicated and "available_count" in dedicated) else body.get("rate_limit_reset_credits")
        if not isinstance(source, dict):
            return None

        count_raw = cls._parse_float(source.get("available_count"))
        if count_raw is None:
            return None

        count = max(0, int(count_raw))
        expiries: List[datetime] = []

        credits_list = source.get("credits")
        if isinstance(credits_list, list):
            for c in credits_list:
                if isinstance(c, dict):
                    status = c.get("status")
                    if status and status != "available":
                        continue
                    exp = cls._parse_date(c.get("expires_at"))
                    if exp:
                        expiries.append(exp)

        expiries.sort()
        return count, expiries

    @classmethod
    def _read_credits_remaining(cls, body: Dict[str, Any], headers: Dict[str, str]) -> Optional[float]:
        credits = body.get("credits")
        if isinstance(credits, dict):
            bal = cls._parse_float(credits.get("balance"))
            if bal is not None:
                return bal
            if credits.get("has_credits") is False:
                return 0.0

        header_bal = cls._parse_float(headers.get("x-codex-credits-balance"))
        return header_bal

    @staticmethod
    def _parse_float(val: Any) -> Optional[float]:
        if val is None or isinstance(val, bool):
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_date(val: Any) -> Optional[datetime]:
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            epoch = float(val)
            # Cursor-style millisecond timestamps are > year 2286 in seconds.
            if epoch > 1e12:
                epoch /= 1000.0
            try:
                return datetime.fromtimestamp(epoch, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                pass
        return None
