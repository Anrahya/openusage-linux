"""Codex Authentication Store, JWT Expiry Detection, and Token Auto-Refresh."""

from __future__ import annotations
import base64
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class CodexAuthError(Exception):
    pass


@dataclass
class CodexTokens:
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    id_token: Optional[str] = None
    account_id: Optional[str] = None


@dataclass
class CodexAuth:
    tokens: Optional[CodexTokens] = None
    last_refresh: Optional[str] = None
    api_key: Optional[str] = None
    auth_mode: Optional[str] = None


@dataclass
class CodexAuthState:
    auth: CodexAuth
    file_path: str

    @property
    def has_usable_access_token(self) -> bool:
        return bool(self.auth.tokens and self.auth.tokens.access_token and self.auth.tokens.access_token.strip())


class CodexAuthStore:
    CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    REFRESH_URL = "https://auth.openai.com/oauth/token"
    ACCESS_TOKEN_REFRESH_WINDOW_SEC = 300  # 5 minutes

    def __init__(self, codex_home: Optional[str] = None):
        self._custom_codex_home = codex_home

    def get_candidate_paths(self) -> List[str]:
        codex_home = self._custom_codex_home or os.environ.get("CODEX_HOME")
        if codex_home and codex_home.strip():
            paths = [os.path.join(os.path.expanduser(p.strip()), "auth.json") for p in codex_home.split(",") if p.strip()]
            return paths
        
        defaults = [
            os.path.expanduser("~/.config/codex/auth.json"),
            os.path.expanduser("~/.codex/auth.json"),
        ]
        return defaults

    def load_auth_candidates(self) -> List[CodexAuthState]:
        candidates: List[CodexAuthState] = []
        for path in self.get_candidate_paths():
            state = self.load_auth(path)
            if state:
                candidates.append(state)
        return candidates

    def load_auth(self, path: str) -> Optional[CodexAuthState]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            tokens_data = data.get("tokens")
            tokens = None
            if isinstance(tokens_data, dict):
                tokens = CodexTokens(
                    access_token=tokens_data.get("access_token"),
                    refresh_token=tokens_data.get("refresh_token"),
                    id_token=tokens_data.get("id_token"),
                    account_id=tokens_data.get("account_id"),
                )
            
            auth = CodexAuth(
                tokens=tokens,
                last_refresh=data.get("last_refresh"),
                api_key=data.get("OPENAI_API_KEY"),
                auth_mode=data.get("auth_mode"),
            )
            return CodexAuthState(auth=auth, file_path=path)
        except Exception:
            return None

    def save_auth(self, state: CodexAuthState):
        """Atomically saves auth data to file with 0600 permissions."""
        target_path = Path(state.file_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload: Dict[str, Any] = {}
        if state.auth.auth_mode:
            payload["auth_mode"] = state.auth.auth_mode
        if state.auth.api_key:
            payload["OPENAI_API_KEY"] = state.auth.api_key
        if state.auth.last_refresh:
            payload["last_refresh"] = state.auth.last_refresh

        if state.auth.tokens:
            payload["tokens"] = {
                "access_token": state.auth.tokens.access_token,
                "refresh_token": state.auth.tokens.refresh_token,
                "id_token": state.auth.tokens.id_token,
                "account_id": state.auth.tokens.account_id,
            }

        temp_dir = str(target_path.parent)
        fd, temp_path = tempfile.mkstemp(dir=temp_dir, prefix="auth_", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(temp_path, str(target_path))
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise CodexAuthError(f"Failed to persist rotated credentials: {e}")

    @classmethod
    def get_jwt_expiry(cls, jwt_token: str) -> Optional[float]:
        try:
            parts = jwt_token.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
            data = json.loads(payload_json)
            exp = data.get("exp")
            if isinstance(exp, (int, float)):
                return float(exp)
        except Exception:
            pass
        return None

    def needs_refresh(self, auth: CodexAuth) -> bool:
        if auth.tokens and auth.tokens.access_token:
            exp = self.get_jwt_expiry(auth.tokens.access_token)
            if exp is not None:
                now_epoch = time.time()
                return (exp - now_epoch) <= self.ACCESS_TOKEN_REFRESH_WINDOW_SEC

        if auth.last_refresh:
            try:
                dt = datetime.fromisoformat(auth.last_refresh.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
                return age_days > 8.0
            except Exception:
                pass
        return False

    def refresh_access_token(self, state: CodexAuthState) -> str:
        if not state.auth.tokens or not state.auth.tokens.refresh_token:
            raise CodexAuthError("No refresh token available. Run `codex` to log in.")

        refresh_token = state.auth.tokens.refresh_token
        body_data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": self.CLIENT_ID,
            "refresh_token": refresh_token,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.REFRESH_URL,
            data=body_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "OpenUsage-Linux",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                new_access_token = resp_data.get("access_token")
                new_refresh_token = resp_data.get("refresh_token")
                new_id_token = resp_data.get("id_token")

                if not new_access_token:
                    raise CodexAuthError("Invalid refresh response from OpenAI.")

                state.auth.tokens.access_token = new_access_token
                if new_refresh_token:
                    state.auth.tokens.refresh_token = new_refresh_token
                if new_id_token:
                    state.auth.tokens.id_token = new_id_token

                state.auth.last_refresh = datetime.now(timezone.utc).isoformat()
                self.save_auth(state)
                return new_access_token

        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="ignore")
            err_code = None
            try:
                err_json = json.loads(err_text)
                err_code = (
                    err_json.get("error", {}).get("code")
                    if isinstance(err_json.get("error"), dict)
                    else err_json.get("error") or err_json.get("code")
                )
            except Exception:
                pass

            if err_code == "refresh_token_expired":
                raise CodexAuthError("Session expired. Run `codex` to log in again.")
            elif err_code == "refresh_token_reused":
                raise CodexAuthError("Token conflict. Run `codex` to log in again.")
            elif err_code == "refresh_token_invalidated":
                raise CodexAuthError("Token revoked. Run `codex` to log in again.")
            else:
                raise CodexAuthError(f"OAuth refresh failed with HTTP {e.code}: {err_text}")
        except Exception as e:
            raise CodexAuthError(f"Network error refreshing token: {e}")
