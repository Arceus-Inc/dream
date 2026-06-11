"""Per-session memory wiring (spec 11 substrate).

Carries the session's :class:`~dream.contracts.memory.MemoryStore` to the
``memory_search`` / ``memory_get`` tools through the generic
``ToolExecutionContext.metadata`` channel, so the engine stays memory-agnostic
and the tools read a typed bundle rather than poking ``Any``. Mirrors the
skill wiring in :mod:`dream.skills._session`.
"""

from __future__ import annotations

from dataclasses import dataclass

from dream.contracts.memory import MemoryStore

MEMORY_CONTEXT_KEY = "memory_context"


@dataclass(frozen=True)
class MemoryContext:
    """The per-session memory state a memory tool call needs."""

    store: MemoryStore


def put_memory_context(
    metadata: dict[str, object], memory_context: MemoryContext
) -> None:
    """Place ``memory_context`` into a tool ``metadata`` dict under the known key."""
    metadata[MEMORY_CONTEXT_KEY] = memory_context


def read_memory_context(metadata: dict[str, object]) -> MemoryContext | None:
    """Return the :class:`MemoryContext` from tool ``metadata``, or ``None``."""
    value = metadata.get(MEMORY_CONTEXT_KEY)
    return value if isinstance(value, MemoryContext) else None


__all__ = [
    "MEMORY_CONTEXT_KEY",
    "MemoryContext",
    "put_memory_context",
    "read_memory_context",
]
