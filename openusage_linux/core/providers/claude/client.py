"""Claude usage API client + OAuth token refresh (port of ClaudeUsageClient)."""

from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from openusage_linux.core.providers.claude.auth import (
    PROD_CLIENT_ID,
    PROD_REFRESH_URL,
    REFRESH_SCOPE,
    ClaudeAuthError,
    ClaudeAuthState,
    save,
)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.1.69"
BETA_HEADER = "oauth-2025-04-20"


class ClaudeClientError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None,
                 retry_after_seconds: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(headers: Dict[str, str]) -> Optional[float]:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        value = int(raw)
        return float(value) if value >= 0 else None
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return max(0.0, (dt.timestamp() - time.time()))
    except Exception:
        return None


def fetch_usage(access_token: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": BETA_HEADER,
        "User-Agent": USER_AGENT,
    }
    req = urllib.request.Request(USAGE_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.loads(resp.read().decode("utf-8")), resp_headers
    except urllib.error.HTTPError as e:
        resp_headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        retry_after = _retry_after_seconds(resp_headers) if e.code == 429 else None
        raise ClaudeClientError(f"HTTP {e.code}", status_code=e.code, retry_after_seconds=retry_after)
    except Exception as e:
        raise ClaudeClientError(f"Connection failed: {e}")


def refresh_access_token(state: ClaudeAuthState) -> str:
    """Rotate the access token via Claude's OAuth endpoint; persist on success."""
    oauth = state.oauth
    if not oauth.refresh_token or not oauth.refresh_token.strip():
        raise ClaudeAuthError("Token expired. Run `claude` to log in again.", allows_fallback=True)

    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": oauth.refresh_token,
        "client_id": PROD_CLIENT_ID,
        "scope": REFRESH_SCOPE,
    }).encode("utf-8")
    req = urllib.request.Request(
        PROD_REFRESH_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            err_text = e.read().decode("utf-8", errors="ignore")
            try:
                err_data = json.loads(err_text)
                code = err_data.get("error") or err_data.get("error_description")
            except Exception:
                code = None
            if code == "invalid_grant":
                raise ClaudeAuthError(
                    "Session expired. Run `claude` to log in again.", allows_fallback=True
                )
        raise ClaudeAuthError(f"OAuth refresh failed (HTTP {e.code}).")
    except Exception as e:
        raise ClaudeAuthError(f"Network error refreshing token: {e}")

    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ClaudeAuthError("Invalid refresh response from Claude.")

    oauth.access_token = access_token
    if body.get("refresh_token"):
        oauth.refresh_token = body["refresh_token"]
    expires_in = body.get("expires_in")
    if isinstance(expires_in, (int, float)):
        oauth.expires_at_ms = time.time() * 1000 + expires_in * 1000
    save(state)  # persistence failure is nonfatal
    return access_token
