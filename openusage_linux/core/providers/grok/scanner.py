"""Grok CLI log scanner (port of GrokLogUsageScanner)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from openusage_linux.core.base import ProviderUsageHistory


class GrokLogUsageScanner:
    def log_path(self) -> str:
        override = os.environ.get("GROK_HOME", "").strip()
        if override:
            return os.path.join(os.path.expanduser(override), "logs", "unified.jsonl")
        return os.path.join(os.path.expanduser("~"), ".grok", "logs", "unified.jsonl")

    def scan(self, days_back: int = 30, now: Optional[datetime] = None) -> Optional[ProviderUsageHistory]:
        # No official Grok CLI log on this machine yet; spend tiles stay "No data".
        # OpenCode xAI rows stay on the OpenCode card so they are not double-counted.
        if not os.path.exists(self.log_path()):
            return None
        return None
