"""Pluggable LLM provider interface.

A provider turns an :class:`LLMRequest` (system prompt + user prompt) into raw text.
Parsing, validation, retries, caching and fallbacks are handled one level up in
``market_sim.agents.decision`` so every provider gets them for free.

To add a provider, subclass :class:`LLMProvider` and decorate it with
``@register_provider("name")``; then set ``llm.provider: name`` in an experiment config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..config import LLMConfig


class LLMError(RuntimeError):
    """Raised by providers for any failure that should trigger a retry / fallback."""


@dataclass
class LLMRequest:
    system: str
    user: str
    model: str
    max_tokens: int = 1024
    temperature: Optional[float] = None
    effort: Optional[str] = None
    # Optional JSON schema for the expected response (providers may use structured output)
    response_schema: Optional[dict[str, Any]] = None
    # Structured context (observation, strategy...). Never sent to remote providers and
    # not part of the cache key; the mock provider uses it to synthesise answers.
    metadata: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    cached: bool = False


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


_REGISTRY: dict[str, type[LLMProvider]] = {}


def register_provider(name: str) -> Callable[[type[LLMProvider]], type[LLMProvider]]:
    def deco(cls: type[LLMProvider]) -> type[LLMProvider]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return deco


def create_provider(config: LLMConfig) -> LLMProvider:
    # Import built-in providers so they self-register.
    from . import anthropic_provider, mock  # noqa: F401

    try:
        cls = _REGISTRY[config.provider]
    except KeyError as exc:
        raise ValueError(f"unknown LLM provider '{config.provider}'. Known: {sorted(_REGISTRY)}") from exc
    return cls(config)


def available_providers() -> list[str]:
    from . import anthropic_provider, mock  # noqa: F401
    return sorted(_REGISTRY)
