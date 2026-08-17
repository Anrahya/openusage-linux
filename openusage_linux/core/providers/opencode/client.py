"""OpenCode Go usage API client (port of OpenCodeUsageClient)."""

from __future__ import annotations
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


class OpenCodeClientError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def error_type(body: str) -> Optional[str]:
    try:
        data = json.loads(body)
    except Exception:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        kind = error.get("type")
        if isinstance(kind, str) and kind.strip():
            return kind.strip()
    return None


def fetch_usage(api_key: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Accept": "application/json",
        "User-Agent": "OpenUsage-Linux",
    }
    req = urllib.request.Request(USAGE_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.loads(resp.read().decode("utf-8")), resp_headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise OpenCodeClientError(f"HTTP {e.code}", status_code=e.code, body=body)
    except Exception as e:
        raise OpenCodeClientError(f"Connection failed: {e}")
