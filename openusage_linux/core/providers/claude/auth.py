"""Claude auth store — reads Claude Code's OAuth credentials (port of ClaudeAuthStore).

Linux credential sources, in precedence order:
1. $CLAUDE_CONFIG_DIR/.credentials.json (or ~/.claude/.credentials.json)
2. CLAUDE_CODE_OAUTH_TOKEN env var (inference-only: cannot read usage limits)
"""

from __future__ import annotations
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ClaudeAuthError(Exception):
    def __init__(self, message: str, allows_fallback: bool = False):
        super().__init__(message)
        self.allows_fallback = allows_fallback


USAGE_SCOPE = "user:profile"
REFRESH_MARGIN_MS = 5 * 60 * 1000

PROD_REFRESH_URL = "https://platform.claude.com/v1/oauth/token"
PROD_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_SCOPE = (
    "user:profile user:inference user:sessions:claude_code "
    "user:mcp_servers user:file_upload"
)


@dataclass
class ClaudeOAuth:
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at_ms: Optional[float] = None
    subscription_type: Optional[str] = None
    rate_limit_tier: Optional[str] = None
    scopes: Optional[List[str]] = None

    @property
    def has_usable_access_token(self) -> bool:
        return bool(self.access_token and self.access_token.strip())

    def live_usage_available(self, inference_only: bool = False) -> str:
        if inference_only:
            return "inference_only"
        if not self.scopes:
            return "available"  # legacy tokens without scope lists are allowed
        if USAGE_SCOPE in self.scopes:
            return "available"
        return "missing_profile_scope"


@dataclass
class ClaudeAuthState:
    oauth: ClaudeOAuth
    source: str  # "file" | "environment"
    file_path: Optional[str] = None
    full_data: Dict[str, Any] = field(default_factory=dict)


def credential_path() -> str:
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if override:
        return os.path.join(os.path.expanduser(override), ".credentials.json")
    return os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")


def _parse_oauth(data: Dict[str, Any]) -> ClaudeOAuth:
    return ClaudeOAuth(
        access_token=data.get("accessToken"),
        refresh_token=data.get("refreshToken"),
        expires_at_ms=data.get("expiresAt") if isinstance(data.get("expiresAt"), (int, float)) else None,
        subscription_type=data.get("subscriptionType"),
        rate_limit_tier=data.get("rateLimitTier"),
        scopes=data.get("scopes") if isinstance(data.get("scopes"), list) else None,
    )


def load_candidates() -> List[ClaudeAuthState]:
    """Stored file login first; env token appended last as inference-only fallback."""
    candidates: List[ClaudeAuthState] = []

    path = credential_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            oauth_data = data.get("claudeAiOauth") if isinstance(data, dict) else None
            if isinstance(oauth_data, dict):
                oauth = _parse_oauth(oauth_data)
                if oauth.has_usable_access_token:
                    candidates.append(ClaudeAuthState(
                        oauth=oauth, source="file", file_path=path, full_data=data,
                    ))
        except Exception:
            pass

    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env_token:
        base = candidates[0].oauth if candidates else ClaudeOAuth()
        candidates.append(ClaudeAuthState(
            oauth=ClaudeOAuth(
                access_token=env_token,
                subscription_type=base.subscription_type,
                scopes=base.scopes,
            ),
            source="environment",
        ))
    return candidates


def needs_refresh(oauth: ClaudeOAuth) -> bool:
    if oauth.expires_at_ms is None:
        return False
    return oauth.expires_at_ms - time.time() * 1000 <= REFRESH_MARGIN_MS


def save(state: ClaudeAuthState) -> bool:
    """Rewrite the credentials file, preserving other top-level keys. 0600 atomic."""
    if state.source != "file" or not state.file_path:
        return False
    payload = dict(state.full_data)
    payload["claudeAiOauth"] = {
        "accessToken": state.oauth.access_token,
        "refreshToken": state.oauth.refresh_token,
        "expiresAt": state.oauth.expires_at_ms,
        **({"subscriptionType": state.oauth.subscription_type} if state.oauth.subscription_type else {}),
        **({"rateLimitTier": state.oauth.rate_limit_tier} if state.oauth.rate_limit_tier else {}),
        **({"scopes": state.oauth.scopes} if state.oauth.scopes else {}),
    }
    target_dir = os.path.dirname(state.file_path) or "."
    fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=".credentials_", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(temp_path, state.file_path)
        return True
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return False
