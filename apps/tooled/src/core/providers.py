from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

__all__ = [
    "OpenAICompatProvider",
    "Provider",
    "get_provider",
    "list_providers",
    "register_provider",
]


class Provider(Protocol):
    """Minimum surface a provider must expose to build a chat HTTP client."""

    name: str
    base_url: str
    api_key: str

    def build_client(
        self,
        connect_timeout: float,
        read_timeout: float,
    ) -> httpx.AsyncClient: ...


@dataclass
class OpenAICompatProvider:
    """OpenAI-compatible wire format (OpenAI, Mistral, GLM, tool-capable Ollama)."""

    name: str
    base_url: str
    api_key: str
    extra_headers: dict[str, str] = field(default_factory=dict)

    def build_client(
        self,
        connect_timeout: float,
        read_timeout: float,
    ) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=connect_timeout,
                pool=connect_timeout,
            ),
        )


_PROVIDERS: dict[str, Provider] = {}


def register_provider(p: Provider) -> None:
    _PROVIDERS[p.name] = p


def get_provider(name: str) -> Provider:
    if name not in _PROVIDERS:
        raise KeyError(f"Unknown provider {name!r}. Registered: {list(_PROVIDERS)}")
    return _PROVIDERS[name]


def list_providers() -> list[str]:
    return list(_PROVIDERS)
