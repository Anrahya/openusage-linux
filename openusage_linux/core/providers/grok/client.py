"""Grok billing client (port of GrokUsageClient)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

CREDITS_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"
REFRESH_URL = "https://auth.x.ai/oauth2/token"
TOKEN_AUTH_HEADER = "xai-grok-cli"


def refresh_form_body(refresh_token: str, client_id: str) -> str:
    return (
        "grant_type=refresh_token"
        f"&client_id={urllib.parse.quote(client_id, safe='')}"
        f"&refresh_token={urllib.parse.quote(refresh_token, safe='')}"
    )


class GrokClientError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _auth_headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token.strip()}",
        "X-XAI-Token-Auth": TOKEN_AUTH_HEADER,
        "Accept": "application/json",
        "User-Agent": "OpenUsage",
    }


def _get_json(url: str, headers: Dict[str, str], timeout: int = 10) -> Tuple[Dict[str, Any], Dict[str, str]]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.loads(resp.read().decode("utf-8")), resp_headers
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore")
        raise GrokClientError(
            f"Grok billing request failed (HTTP {error.code}). Try again later.",
            status_code=error.code,
            body=body,
        ) from error
    except Exception as error:
        raise GrokClientError("Grok billing request failed. Check your connection.") from error


class GrokUsageClient:
    def fetch_credits_config(self, access_token: str) -> Dict[str, Any]:
        body, _ = _get_json(CREDITS_URL, _auth_headers(access_token))
        if not isinstance(body, dict):
            raise GrokClientError("Grok billing response changed.")
        return body

    def fetch_settings(self, access_token: str) -> Dict[str, Any]:
        try:
            body, _ = _get_json(SETTINGS_URL, _auth_headers(access_token))
        except GrokClientError:
            return {}
        return body if isinstance(body, dict) else {}

    def refresh_token(self, refresh_token: str, client_id: str) -> Dict[str, Any]:
        payload = refresh_form_body(refresh_token, client_id).encode("utf-8")
        req = urllib.request.Request(
            REFRESH_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise GrokClientError(
                f"Grok billing request failed (HTTP {error.code}). Try again later.",
                status_code=error.code,
            ) from error
        except Exception as error:
            raise GrokClientError("Grok billing request failed. Check your connection.") from error
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise GrokClientError("Grok billing response changed.")
        return body
