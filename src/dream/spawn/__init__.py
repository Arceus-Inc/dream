"""dream.spawn — runtime-scoped child session support.

Provides the spawn_subagent tool's context carrier (SpawnContext), budget
enforcement (SpawnBudget), and outcome type (SpawnOutcome). These are the
three pieces the tool and the factory both touch; everything else stays
internal to the sub-modules.
"""

from __future__ import annotations

from dream.spawn._context import (
    MAX_SPAWNS_PER_SESSION,
    SPAWN_CONTEXT_KEY,
    SpawnBudget,
    SpawnContext,
    SpawnUnknownToolsError,
    read_spawn_context,
)
from dream.spawn._outcome import SpawnOutcome

__all__ = [
    "MAX_SPAWNS_PER_SESSION",
    "SPAWN_CONTEXT_KEY",
    "SpawnBudget",
    "SpawnContext",
    "SpawnOutcome",
    "SpawnUnknownToolsError",
    "read_spawn_context",
]
