"""Cursor credential store — reads Cursor's Electron state database (port of CursorAuthStore).

On Linux Cursor keeps its auth in ~/.config/Cursor/User/globalStorage/state.vscdb
(SQLite, table ItemTable). Tokens encrypted with Electron safeStorage live in
libsecret; the SQLite path alone covers most installs and degrades gracefully.
"""

from __future__ import annotations
import base64
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

REFRESH_BUFFER_SEC = 5 * 60
ACCESS_TOKEN_KEY = "cursorAuth/accessToken"
REFRESH_TOKEN_KEY = "cursorAuth/refreshToken"
MEMBERSHIP_KEY = "cursorAuth/stripeMembershipType"
CACHED_EMAIL_KEY = "cursorAuth/cachedEmail"

PLAN_LABELS = {
    "free": "Free",
    "hobby": "Hobby",
    "pro": "Pro",
    "pro_plus": "Pro+",
    "ultra": "Ultra",
    "business": "Business",
    "team": "Team",
    "enterprise": "Enterprise",
}


@dataclass
class CursorAuthState:
    access_token: Optional[str]
    refresh_token: Optional[str]
    membership_type: Optional[str]
    db_path: str
    cached_email: Optional[str] = None

    def plan_label(self) -> Optional[str]:
        if not self.membership_type:
            return None
        key = self.membership_type.strip().lower()
        if key in PLAN_LABELS:
            return PLAN_LABELS[key]
        return " ".join(part[:1].upper() + part[1:] for part in key.replace("-", "_").split("_") if part)


def state_db_paths() -> list:
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return [
        os.path.join(config_home, "Cursor", "User", "globalStorage", "state.vscdb"),
        os.path.join(os.path.expanduser("~"), ".cursor", "User", "globalStorage", "state.vscdb"),
    ]


def _read_key(db_path: str, key: str) -> Optional[str]:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1", (key,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    value = str(row[0]).strip()
    return value or None


def load_auth_state() -> Optional[CursorAuthState]:
    for path in state_db_paths():
        if not os.path.exists(path):
            continue
        access_token = _read_key(path, ACCESS_TOKEN_KEY)
        refresh_token = _read_key(path, REFRESH_TOKEN_KEY)
        membership = _read_key(path, MEMBERSHIP_KEY)
        if membership:
            membership = membership.lower()
        cached_email = _read_key(path, CACHED_EMAIL_KEY)
        if access_token or refresh_token:
            return CursorAuthState(
                access_token=access_token,
                refresh_token=refresh_token,
                membership_type=membership,
                db_path=path,
                cached_email=cached_email,
            )
    return None


def save_access_token(state: CursorAuthState, access_token: str) -> bool:
    """Persist a rotated token back into the state DB. Nonfatal on failure."""
    try:
        conn = sqlite3.connect(state.db_path, timeout=2)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                (ACCESS_TOKEN_KEY, access_token),
            )
            conn.commit()
        finally:
            conn.close()
        state.access_token = access_token
        return True
    except Exception:
        return False


def jwt_claim(token: str, claim: str) -> Optional[object]:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))
        return data.get(claim)
    except Exception:
        return None


def token_subject(token: str) -> Optional[str]:
    sub = jwt_claim(token, "sub")
    if isinstance(sub, str) and sub.strip():
        return sub.strip()
    return None


def user_id_from_token(token: str) -> Optional[str]:
    subject = token_subject(token)
    if not subject:
        return None
    parts = subject.split("|")
    return parts[1] if len(parts) > 1 else parts[0]


def needs_refresh(token: Optional[str]) -> bool:
    if not token:
        return True
    exp = jwt_claim(token, "exp")
    if not isinstance(exp, (int, float)):
        return True
    return exp - time.time() <= REFRESH_BUFFER_SEC
