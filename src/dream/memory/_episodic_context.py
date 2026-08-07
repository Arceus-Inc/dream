"""Per-session episodic wiring for ``session_search``.

Mirrors :mod:`dream.memory._context`: the store rides
``ToolExecutionContext.metadata`` so the engine stays store-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass

from dream.contracts.episodic import EpisodicStore

EPISODIC_CONTEXT_KEY = "episodic_context"


@dataclass(frozen=True)
class EpisodicContext:
    """The per-session episodic store a ``session_search`` call needs."""

    store: EpisodicStore


def put_episodic_context(
    metadata: dict[str, object], episodic_context: EpisodicContext
) -> None:
    """Place ``episodic_context`` into tool ``metadata`` under the known key."""
    metadata[EPISODIC_CONTEXT_KEY] = episodic_context


def read_episodic_context(metadata: dict[str, object]) -> EpisodicContext | None:
    """Return the :class:`EpisodicContext` from tool ``metadata``, or ``None``."""
    value = metadata.get(EPISODIC_CONTEXT_KEY)
    return value if isinstance(value, EpisodicContext) else None


__all__ = [
    "EPISODIC_CONTEXT_KEY",
    "EpisodicContext",
    "put_episodic_context",
    "read_episodic_context",
]
