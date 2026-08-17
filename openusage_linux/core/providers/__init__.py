"""Provider registry and catalog."""

from __future__ import annotations
from typing import Any, Dict, List, Type

from openusage_linux.core.providers.codex import CodexProvider
from openusage_linux.core.providers.claude import ClaudeProvider
from openusage_linux.core.providers.cursor import CursorProvider
from openusage_linux.core.providers.opencode import OpenCodeProvider


class ProviderCatalog:
    _registry: Dict[str, Type] = {
        "codex": CodexProvider,
        "claude": ClaudeProvider,
        "cursor": CursorProvider,
        "opencode": OpenCodeProvider,
    }

    @classmethod
    def get_all_providers(cls) -> List[Any]:
        return [provider_cls() for provider_cls in cls._registry.values()]

    @classmethod
    def get_provider(cls, provider_id: str) -> Any:
        provider_cls = cls._registry.get(provider_id.lower())
        if provider_cls:
            return provider_cls()
        return None
