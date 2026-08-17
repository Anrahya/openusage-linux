"""Cursor API client (port of CursorUsageClient)."""

from __future__ import annotations
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"
API2_BASE = "https://api2.cursor.sh"
COOKIE_BASE = "https://cursor.com"


class CursorClientError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _request(url: str, headers: Dict[str, str], method: str = "GET",
             data: Optional[bytes] = None, timeout: int = 10) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        raise CursorClientError(f"HTTP {e.code}", status_code=e.code, body=body.decode("utf-8", errors="ignore"))
    except CursorClientError:
        raise
    except Exception as e:
        raise CursorClientError(f"Connection failed: {e}")


def _parse_json(raw: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise CursorClientError("Usage response invalid. Try again later.")
    if not isinstance(data, dict):
        raise CursorClientError("Usage response invalid. Try again later.")
    return data


def _connect_rpc(path: str, access_token: str, timeout: int = 10) -> Dict[str, Any]:
    url = f"{API2_BASE}/{path}"
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "User-Agent": "OpenUsage-Linux",
    }
    _, _, raw = _request(url, headers, method="POST", data=b"{}", timeout=timeout)
    return _parse_json(raw)


def session_token(access_token: str, user_id: str) -> str:
    return f"{user_id}%3A%3A{access_token.strip()}"


def _cookie_request(path: str, cookie_value: str, accept: str = "application/json",
                    timeout: int = 10) -> bytes:
    headers = {
        "Cookie": f"WorkosCursorSessionToken={cookie_value}",
        "Accept": accept,
        "User-Agent": "OpenUsage-Linux",
    }
    _, _, raw = _request(f"{COOKIE_BASE}{path}", headers, timeout=timeout)
    return raw


def fetch_current_period_usage(access_token: str) -> Dict[str, Any]:
    return _connect_rpc("aiserver.v1.DashboardService/GetCurrentPeriodUsage", access_token)


def fetch_plan_info(access_token: str) -> Optional[str]:
    try:
        body = _connect_rpc("aiserver.v1.DashboardService/GetPlanInfo", access_token)
    except CursorClientError:
        return None
    plan_info = body.get("planInfo")
    if isinstance(plan_info, dict):
        name = plan_info.get("planName")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def fetch_credit_grants(access_token: str) -> Optional[Dict[str, Any]]:
    try:
        body = _connect_rpc("aiserver.v1.DashboardService/GetCreditGrantsBalance", access_token)
    except CursorClientError:
        return None
    if not isinstance(body.get("hasCreditGrants"), bool):
        return None
    total = body.get("totalCents")
    used = body.get("usedCents")
    if body["hasCreditGrants"]:
        if not (isinstance(total, (int, float)) and total > 0 and isinstance(used, (int, float)) and used >= 0):
            return None
    return body


def refresh_access_token(refresh_token: str) -> Tuple[Optional[str], bool]:
    """Returns (new access token or None, shouldLogout)."""
    payload = json.dumps({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }).encode("utf-8")
    try:
        _, _, raw = _request(
            f"{API2_BASE}/oauth/token",
            {"Content-Type": "application/json", "User-Agent": "OpenUsage-Linux"},
            method="POST", data=payload, timeout=15,
        )
    except CursorClientError as e:
        should_logout = False
        if e.status_code in (400, 401) and isinstance(e.body, str):
            try:
                should_logout = bool(json.loads(e.body).get("shouldLogout"))
            except Exception:
                pass
        raise CursorClientError(
            "Session expired. Sign in via Cursor app or run `agent login`." if should_logout
            else "Token expired. Sign in via Cursor app or run `agent login`.",
            status_code=e.status_code,
        )
    body = _parse_json(raw)
    if body.get("shouldLogout") is True:
        raise CursorClientError("Session expired. Sign in via Cursor app or run `agent login`.")
    token = body.get("access_token")
    if isinstance(token, str) and token.strip():
        return token.strip(), False
    return None, False


def fetch_stripe_balance(cookie_value: str) -> int:
    """Negative customerBalance is a credit; return it as positive cents."""
    try:
        raw = _cookie_request("/api/auth/stripe", cookie_value)
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return 0
    balance = body.get("customerBalance")
    if isinstance(balance, (int, float)) and balance < 0:
        return int(abs(balance))
    return 0


def fetch_usage_api(cookie_value: str, user_id: str) -> Optional[Dict[str, Any]]:
    try:
        raw = _cookie_request(f"/api/usage?user={user_id}", cookie_value)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def fetch_usage_csv(cookie_value: str, start_ms: int, end_ms: int) -> Optional[str]:
    try:
        raw = _cookie_request(
            f"/api/dashboard/export-usage-events-csv?startDate={start_ms}&endDate={end_ms}&strategy=tokens",
            cookie_value, accept="text/csv", timeout=30,
        )
        return raw.decode("utf-8")
    except Exception:
        return None
