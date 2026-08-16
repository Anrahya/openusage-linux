"""Codex ChatGPT Wham API Client for usage and reset credits."""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional, Tuple


class CodexClientError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class CodexUsageClient:
    USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
    RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
    CONSUME_RESET_CREDIT_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def fetch_usage(self, access_token: str, account_id: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, str]]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "OpenUsage-Linux",
        }
        if account_id and account_id.strip():
            headers["ChatGPT-Account-Id"] = account_id.strip()

        req = urllib.request.Request(self.USAGE_URL, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                data = json.loads(resp.read().decode("utf-8"))
                return data, resp_headers
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise CodexClientError(f"HTTP {e.code}: {body}", status_code=e.code)
        except Exception as e:
            raise CodexClientError(f"Connection failed: {e}")

    def fetch_reset_credits(self, access_token: str, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "OpenUsage-Linux",
            "OpenAI-Beta": "codex-1",
            "originator": "Codex Desktop",
        }
        if account_id and account_id.strip():
            headers["ChatGPT-Account-Id"] = account_id.strip()

        req = urllib.request.Request(self.RESET_CREDITS_URL, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if 200 <= resp.status < 300:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return None

    def consume_reset_credit(
        self,
        access_token: str,
        credit_id: str,
        redeem_request_id: str,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "OpenUsage-Linux",
            "OpenAI-Beta": "codex-1",
            "originator": "Codex Desktop",
        }
        if account_id and account_id.strip():
            headers["ChatGPT-Account-Id"] = account_id.strip()

        payload = json.dumps({
            "redeem_request_id": redeem_request_id,
            "credit_id": credit_id,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.CONSUME_RESET_CREDIT_URL, data=payload, headers=headers, method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise CodexClientError(f"HTTP {e.code}: {body}", status_code=e.code)
        except Exception as e:
            raise CodexClientError(f"Failed to consume credit: {e}")
