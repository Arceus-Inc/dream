"""Session: one conversation on a Harness.

This module exposes the public types. The substantive turn-loop
implementation lives in `dream.engine` and is wired up later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from dream.events import Event


@dataclass(frozen=True)
class SessionOptions:
    """Per-session overrides. All fields optional."""

    model: str | None = None
    system_prompt: str | None = None
    max_turns: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionCost:
    """Running counters surfaced via `Session.cost`."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


class Session:
    """One conversation against a Harness.

    This is the public type. The concrete engine binding is wired in
    `dream.engine._engine`; placeholder methods raise NotImplementedError
    until that lands.
    """

    id: str
    options: SessionOptions

    def __init__(self, *, id: str, options: SessionOptions | None = None) -> None:
        self.id = id
        self.options = options or SessionOptions()
        self.cost = SessionCost()

    def send(self, prompt: str) -> AsyncIterator[Event]:
        """Submit a user prompt and stream typed events back."""
        raise NotImplementedError("engine binding not yet implemented")

    async def cancel(self) -> None:
        """Cancel the in-flight turn, if any."""
        raise NotImplementedError("engine binding not yet implemented")

    async def close(self) -> None:
        """Release resources held by this session."""
        raise NotImplementedError("engine binding not yet implemented")


__all__ = ["Session", "SessionCost", "SessionOptions"]
