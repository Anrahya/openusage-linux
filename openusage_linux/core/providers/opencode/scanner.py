"""OpenCode usage scanner — reads per-message cost from OpenCode's SQLite logs.

Costs in these databases are authoritative (measured, not estimated), so no
pricing table is applied.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from openusage_linux.core.base import (
    DailyUsageSeries,
    ModelUsageSummary,
    ProviderUsageHistory,
)
from openusage_linux.core.providers.opencode.paths import database_files

HOSTED_PROVIDER_IDS = ("opencode-go", "opencode")
DEFAULT_DAYS_BACK = 30

_LEGACY_DATA_SQL = """
SELECT json_group_array(json_array(
         time_created,
         json_extract(data,'$.cost'),
         COALESCE(json_extract(data,'$.tokens.total'),0),
         json_extract(data,'$.modelID'),
         json_extract(data,'$.providerID')))
FROM message
WHERE time_created >= ?
  AND json_valid(data)
  AND json_extract(data,'$.role') = 'assistant'
  AND json_extract(data,'$.providerID') IN ('opencode-go','opencode')
  AND json_type(data,'$.cost') IN ('integer','real')
"""

_SESSION_DATA_SQL = """
SELECT json_group_array(json_array(
         time_created,
         json_extract(data,'$.cost'),
         COALESCE(
           json_extract(data,'$.tokens.total'),
           COALESCE(json_extract(data,'$.tokens.input'),0)
             + COALESCE(json_extract(data,'$.tokens.output'),0)
             + COALESCE(json_extract(data,'$.tokens.reasoning'),0)
         ),
         COALESCE(json_extract(data,'$.model.id'), json_extract(data,'$.modelID')),
         COALESCE(json_extract(data,'$.model.providerID'), json_extract(data,'$.providerID'))))
FROM session_message
WHERE type = 'assistant'
  AND time_created >= ?
  AND json_valid(data)
  AND COALESCE(json_extract(data,'$.model.providerID'), json_extract(data,'$.providerID'))
      IN ('opencode-go','opencode')
  AND json_type(data,'$.cost') IN ('integer','real')
"""

_LEGACY_PROBE_SQL = """
SELECT 1 FROM message
WHERE json_valid(data)
  AND json_extract(data,'$.role') = 'assistant'
  AND json_extract(data,'$.providerID') IN ('opencode-go','opencode')
  AND json_type(data,'$.cost') IN ('integer','real')
LIMIT 1
"""

_SESSION_PROBE_SQL = """
SELECT 1 FROM session_message
WHERE type = 'assistant'
  AND json_valid(data)
  AND COALESCE(json_extract(data,'$.model.providerID'), json_extract(data,'$.providerID'))
      IN ('opencode-go','opencode')
  AND json_type(data,'$.cost') IN ('integer','real')
LIMIT 1
"""


class OpenCodeScanError(Exception):
    pass


def _open_readonly(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    conn.execute("PRAGMA busy_timeout = 1000")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(name) for (name,) in rows if name}


def _query_rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[List[Any]]:
    raw = conn.execute(sql, params).fetchone()
    if not raw or not raw[0]:
        return []
    rows = json.loads(raw[0])
    return rows if isinstance(rows, list) else []


def has_hosted_usage() -> bool:
    try:
        paths = database_files()
    except OSError:
        return True  # unreadable data dir still counts as a footprint
    for path in paths:
        try:
            conn = _open_readonly(path)
            try:
                tables = _table_names(conn)
                if "session_message" in tables and conn.execute(_SESSION_PROBE_SQL).fetchone():
                    return True
                if "message" in tables and conn.execute(_LEGACY_PROBE_SQL).fetchone():
                    return True
            finally:
                conn.close()
        except Exception:
            continue
    return False


def scan(days_back: int = DEFAULT_DAYS_BACK, now: Optional[datetime] = None) -> Optional[ProviderUsageHistory]:
    now = now or datetime.now(timezone.utc)
    try:
        paths = database_files()
    except OSError as e:
        raise OpenCodeScanError(
            "Couldn't read OpenCode's local database. Quit OpenCode and refresh, "
            "or check the data directory's permissions."
        ) from e
    if not paths:
        return None

    tile_since = (now.astimezone() - timedelta(days=days_back)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cutoff_ms = int(tile_since.timestamp() * 1000)

    entries: List[List[Any]] = []
    failures = 0
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            conn = _open_readonly(path)
            try:
                tables = _table_names(conn)
                if "session_message" in tables:
                    entries.extend(_query_rows(conn, _SESSION_DATA_SQL, (cutoff_ms,)))
                elif "message" in tables:
                    entries.extend(_query_rows(conn, _LEGACY_DATA_SQL, (cutoff_ms,)))
                else:
                    failures += 1
            finally:
                conn.close()
        except Exception:
            failures += 1
            continue

    if failures == len(paths):
        raise OpenCodeScanError(
            "Couldn't read OpenCode's local database. Quit OpenCode and refresh, "
            "or check the data directory's permissions."
        )

    daily: Dict[str, Dict[str, Any]] = {}
    models: Dict[str, Dict[str, Any]] = {}
    daily_models: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for entry in entries:
        if not isinstance(entry, list) or len(entry) < 5:
            continue
        time_ms, cost, tokens_total, model_id, provider_id = entry[:5]
        if isinstance(time_ms, bool) or not isinstance(time_ms, (int, float)):
            continue
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            continue
        if not isinstance(provider_id, str):
            continue
        event_dt = datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc)
        if event_dt < tile_since:
            continue
        if isinstance(tokens_total, bool) or not isinstance(tokens_total, (int, float)):
            tokens_total = 0
        tokens = int(min(max(tokens_total, 0), 1e15))
        model = model_id.strip() if isinstance(model_id, str) and model_id.strip() else "Unattributed"

        day = event_dt.astimezone().date().isoformat()
        bucket = daily.setdefault(day, {"tokens": 0, "cost": 0.0})
        bucket["tokens"] += tokens
        bucket["cost"] += float(cost)
        model_bucket = models.setdefault(model, {"tokens": 0, "cost": 0.0})
        model_bucket["tokens"] += tokens
        model_bucket["cost"] += float(cost)
        day_model = daily_models.setdefault(day, {}).setdefault(model, {"tokens": 0, "cost": 0.0})
        day_model["tokens"] += tokens
        day_model["cost"] += float(cost)

    series = [
        DailyUsageSeries(
            date=day,
            total_tokens=values["tokens"],
            estimated_cost=round(values["cost"], 2),
            models=[
                ModelUsageSummary(
                    model=name,
                    total_tokens=int(model_values["tokens"]),
                    estimated_cost=round(float(model_values["cost"]), 2),
                )
                for name, model_values in sorted(
                    daily_models.get(day, {}).items(),
                    key=lambda item: item[1]["cost"],
                    reverse=True,
                )
            ],
        )
        for day, values in sorted(daily.items())
    ]
    model_usage = [
        ModelUsageSummary(model=name, total_tokens=int(values["tokens"]), estimated_cost=round(float(values["cost"]), 2))
        for name, values in sorted(models.items(), key=lambda item: item[1]["cost"], reverse=True)
    ]
    return ProviderUsageHistory(series=series, model_usage=model_usage)
