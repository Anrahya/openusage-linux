"""Grok auth store — ~/.grok/auth.json, or OpenCode's xai OAuth row."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openusage_linux.core.atomic import atomic_write_json
from openusage_linux.core.providers.opencode.paths import database_files

DEFAULT_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
REFRESH_BUFFER_SECONDS = 5 * 60


class GrokAuthError(Exception):
    pass


@dataclass
class GrokAuthState:
    token: str
    entry_key: str
    entry: Dict[str, Any]
    auth: Dict[str, Any]
    file_path: str
    source: str = "file"


def auth_file_path() -> str:
    override = os.environ.get("GROK_HOME", "").strip()
    if override:
        return os.path.join(os.path.expanduser(override), "auth.json")
    return os.path.join(os.path.expanduser("~"), ".grok", "auth.json")


def _file_candidates() -> List[GrokAuthState]:
    path = auth_file_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return []
    candidates: List[GrokAuthState] = []
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key")
        if not isinstance(token, str) or not token.strip():
            continue
        candidates.append(
            GrokAuthState(
                token=token.strip(),
                entry_key=key,
                entry=entry,
                auth=data,
                file_path=path,
            )
        )
    return candidates


def _opencode_candidates() -> List[GrokAuthState]:
    try:
        paths = database_files()
    except OSError:
        return []
    for path in paths:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                conn.execute("PRAGMA busy_timeout = 1000")
                rows = conn.execute(
                    "SELECT value FROM credential WHERE integration_id = 'xai'"
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            continue
        for (raw,) in rows:
            if not isinstance(raw, str):
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            token = data.get("access")
            if not isinstance(token, str) or not token.strip():
                continue
            return [
                GrokAuthState(
                    token=token.strip(),
                    entry_key="opencode:xai",
                    entry=data,
                    auth={},
                    file_path=path,
                    source="opencode",
                )
            ]
    return []


def load_candidates() -> List[GrokAuthState]:
    return _file_candidates() or _opencode_candidates()


def _trimmed(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def jwt_expiry(token: str) -> Optional[float]:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return None
    exp = data.get("exp") if isinstance(data, dict) else None
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return float(exp)


def client_id_for(state: GrokAuthState) -> str:
    oidc = _trimmed(state.entry.get("oidc_client_id"))
    if oidc:
        return oidc
    parts = state.entry_key.split("::")
    if len(parts) >= 2:
        tail = parts[-1].strip()
        if tail:
            return tail
    return DEFAULT_CLIENT_ID


def refresh_token_for(state: GrokAuthState) -> Optional[str]:
    return _trimmed(state.entry.get("refresh_token")) or _trimmed(state.entry.get("refresh"))


class GrokAuthStore:
    def load_candidates(self) -> List[GrokAuthState]:
        return load_candidates()

    def needs_refresh(self, state: GrokAuthState) -> bool:
        expires_at = jwt_expiry(state.token)
        if expires_at is None:
            raw = _trimmed(state.entry.get("expires_at")) or _trimmed(state.entry.get("expires"))
            if raw:
                try:
                    expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    try:
                        expires_ms = float(raw)
                        expires_at = expires_ms / 1000.0 if expires_ms > 1e12 else expires_ms
                    except ValueError:
                        expires_at = None
            elif isinstance(state.entry.get("expires"), (int, float)) and not isinstance(state.entry.get("expires"), bool):
                expires_ms = float(state.entry["expires"])
                expires_at = expires_ms / 1000.0 if expires_ms > 1e12 else expires_ms
        if expires_at is None:
            return False
        return expires_at - time.time() <= REFRESH_BUFFER_SECONDS

    def refresh_access_token(self, state: GrokAuthState) -> str:
        from openusage_linux.core.providers.grok.client import GrokClientError, GrokUsageClient

        refresh = refresh_token_for(state)
        if not refresh:
            raise GrokAuthError("Grok auth expired. Run `grok login` again.")
        try:
            body = GrokUsageClient().refresh_token(refresh, client_id_for(state))
        except GrokClientError as error:
            raise GrokAuthError("Grok auth expired. Run `grok login` again.") from error
        access = body["access_token"].strip()
        state.token = access
        state.entry["key"] = access
        state.entry["access"] = access
        new_refresh = _trimmed(body.get("refresh_token"))
        if new_refresh:
            state.entry["refresh_token"] = new_refresh
            state.entry["refresh"] = new_refresh
        expires_in = body.get("expires_in")
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
            expires_at = datetime.fromtimestamp(time.time() + float(expires_in), tz=timezone.utc)
            state.entry["expires_at"] = expires_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            state.entry["expires"] = int(expires_at.timestamp() * 1000)
        try:
            self.save(state)
        except GrokAuthError:
            pass
        return access

    def save(self, state: GrokAuthState) -> None:
        if state.source == "opencode":
            self._save_opencode(state)
            return
        path = state.file_path or auth_file_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    auth_object = json.load(handle)
            except Exception as error:
                raise GrokAuthError("Grok auth invalid. Run `grok login` again.") from error
            if not isinstance(auth_object, dict):
                raise GrokAuthError("Grok auth invalid. Run `grok login` again.")
        else:
            auth_object = dict(state.auth) if isinstance(state.auth, dict) else {}
        entry = auth_object.get(state.entry_key)
        if not isinstance(entry, dict):
            entry = {}
        entry.update(state.entry)
        entry["key"] = state.token
        auth_object[state.entry_key] = entry
        atomic_write_json(path, auth_object, mode=0o600, indent=2)

    def _save_opencode(self, state: GrokAuthState) -> None:
        payload = dict(state.entry)
        payload["access"] = state.token
        try:
            conn = sqlite3.connect(state.file_path, timeout=2)
            try:
                conn.execute("PRAGMA busy_timeout = 1000")
                conn.execute(
                    "UPDATE credential SET value = ? WHERE integration_id = 'xai'",
                    (json.dumps(payload),),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as error:
            raise GrokAuthError("Grok auth invalid. Run `grok login` again.") from error
