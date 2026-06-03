"""Provider Protocol: the substrate the agent loop talks to.

Concrete providers (anthropic, openai-compatible, plugins) live elsewhere
and conform to this Protocol. Capability advertisement lets the loop
adapt without per-provider branching.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from dream.contracts.tool import Tool


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider supports. Loop branches off these flags."""

    tool_use: bool = True
    streaming: bool = True
    prompt_cache: bool = False
    thinking_blocks: bool = False
    parallel_tool_calls: bool = False
    max_context_tokens: int | None = None


@dataclass(frozen=True)
class ProviderUsage:
    """Token / cost counters returned per turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderEvent:
    """One streamed event from a provider.

    `type` is a discriminator. Concrete payload lives in `data` to keep
    this Protocol decoupled from any single provider's event taxonomy.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """An LLM provider the engine streams messages through."""

    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def stream_messages(
        self,
        history: Sequence[dict[str, Any]],
        *,
        system: str,
        tools: Sequence[Tool],
        model: str,
        max_output_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[ProviderEvent]: ...
