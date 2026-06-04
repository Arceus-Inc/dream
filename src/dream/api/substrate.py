"""Substrate Protocol — Spec 02 decisions 5 & 6.

A *substrate* is one fallible LLM endpoint (OpenAI, Azure OpenAI, Anthropic,
LiteLLM, vLLM, Ollama, …). The runner talks to substrates only through this
Protocol so it can fail over between them without per-substrate branching.

**Exactly five methods. A sixth requires a spec amendment.**

This keeps the surface narrow enough that:

- adding a substrate is a new adapter, not a runner change;
- the cooldown ladder / failover logic (Stage 3) operates on one shape;
- the engine's richer streaming :pyclass:`dream.contracts.provider.Provider`
  Protocol can wrap a substrate without inheriting its quirks.

Substrate is structural (a :pyclass:`typing.Protocol`, not an ABC) so
operator-supplied adapters under ``.dream/substrate-adapters/`` don't need
to import or inherit from dream.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

HealthState = Literal["ok", "degraded", "down"]


@dataclass(frozen=True)
class CompletionResult:
    """One non-streaming substrate response."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthReport:
    """Substrate liveness signal — drives the cooldown ladder + heartbeat."""

    state: HealthState
    detail: str = ""
    latency_ms: float = 0.0


@runtime_checkable
class Substrate(Protocol):
    """The five-method substrate surface. Nothing else.

    Order in the body intentionally matches the spec's enumeration so the
    ``inspect``-based contract test reads as a literal pin against the spec.
    """

    name: str

    def complete(self, prompt: str, params: dict[str, Any] | None = None) -> CompletionResult: ...

    def stream(self, prompt: str, params: dict[str, Any] | None = None) -> Iterator[str]: ...

    def count_tokens(self, text: str) -> int: ...

    def max_window(self) -> int: ...

    def health(self) -> HealthReport: ...
