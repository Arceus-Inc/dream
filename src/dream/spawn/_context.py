"""Per-session spawn wiring: SpawnContext, SpawnBudget, and helpers.

Mirrors the per-surface session-context convention used by
``dream.skills._session`` and ``dream.sandbox._session``: a typed bundle
rides ``ToolExecutionContext.metadata`` under a stable key, the tool reads
it out, and the factory stashes it in at session-construction time.

Why a separate package rather than a file?  The spawn surface will grow
(budget policy, background-mode seam) and splitting it now keeps each
module to a single responsibility.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dream.spawn._outcome import SpawnOutcome

SPAWN_CONTEXT_KEY = "spawn_context"
"""Metadata dict key under which SpawnContext is stashed."""

MAX_SPAWNS_PER_SESSION = 16
"""Hard cap: a parent session may spawn at most this many children."""


class SpawnUnknownToolsError(ValueError):
    """No usable tool names were requested for a child.

    Raised by the spawn closure BEFORE any child session starts, so the
    parent's turn gets a recoverable tool error (with the available names)
    instead of paying for a doomed, tool-less child. Live models sometimes
    emit wire-format names (``functions.read_file``); the closure normalizes
    those first — this error means nothing survived even after normalization.
    """

    def __init__(self, *, unknown: list[str], available: list[str]) -> None:
        self.unknown = unknown
        self.available = available
        super().__init__(
            f"none of the requested tools exist: {unknown!r}; "
            f"available tools include {available!r}"
        )


class SpawnBudget:
    """Mutable per-session counter that enforces the spawn cap.

    Mutable by design (counter advances on each successful acquire).
    Not a frozen dataclass because the state must change in-place as the
    tool calls arrive; a new object per call would break cap enforcement.
    """

    def __init__(self, cap: int = MAX_SPAWNS_PER_SESSION) -> None:
        self.cap = cap
        self.used: int = 0

    def acquire(self) -> bool:
        """Attempt to consume one slot.

        Returns ``True`` if the slot was granted (``used`` incremented).
        Returns ``False`` when the cap is already reached; ``used`` is
        NOT incremented on refusal.
        """
        if self.used >= self.cap:
            return False
        self.used += 1
        return True


# Async spawn closure type: the factory builds one per session and closes over
# the harness so the tool never holds a reference to the harness directly.
SpawnClosure = Callable[
    [str, "list[str] | None", "str | None", "int | None"],
    Awaitable["SpawnOutcome"],
]

# The async callable the factory uses to fire SUBAGENT_STOP via the hook executor.
FireSubagentStop = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class SpawnContext:
    """The per-session spawn state the spawn_subagent tool reads.

    ``spawn`` is the async closure built by the factory that calls
    ``harness.run_role`` with the synthesized subagent manifest.

    ``budget`` enforces the session-level spawn cap (MAX_SPAWNS_PER_SESSION).

    ``emit`` is the observer bridge: when present, the closure calls it with
    ``spawn.started`` / ``spawn.completed`` dicts before/after each child run.

    ``fire_subagent_stop`` fires the SUBAGENT_STOP lifecycle hook through the
    session's HookExecutor after each child completes.
    """

    spawn: SpawnClosure
    budget: SpawnBudget
    emit: Callable[[dict[str, Any]], None] | None = None
    fire_subagent_stop: FireSubagentStop | None = None


def read_spawn_context(metadata: dict[str, Any]) -> SpawnContext | None:
    """Return the :class:`SpawnContext` from tool metadata, or ``None``.

    ``None`` means spawning is unavailable in this session (child session,
    or ``build_harness(spawn=False)``). The tool handles this gracefully
    with a three-part error rather than raising.
    """
    value = metadata.get(SPAWN_CONTEXT_KEY)
    return value if isinstance(value, SpawnContext) else None


__all__ = [
    "MAX_SPAWNS_PER_SESSION",
    "SPAWN_CONTEXT_KEY",
    "FireSubagentStop",
    "SpawnBudget",
    "SpawnClosure",
    "SpawnContext",
    "SpawnUnknownToolsError",
    "read_spawn_context",
]
