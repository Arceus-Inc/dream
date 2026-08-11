"""Task-level subagent value objects (spawn config + planner handoff).

The executor backends (in-process, subprocess, worktree, mailbox) went with
the deleted always-on runtime; subagents execute via
:mod:`dream.subagents._inline_executor` + the ``spawn_subagent`` tool.
"""

from dream.swarm._handoff import HandoffArtefact, HandoffEvent, handoff_event
from dream.swarm._spawn import (
    MAX_SUBAGENT_DEPTH,
    BackendType,
    BridgeDisabled,
    SpawnResult,
    SubagentTaskType,
    TeammateExecutor,
    TeammateSpawnConfig,
)

__all__ = [
    "MAX_SUBAGENT_DEPTH",
    "BackendType",
    "BridgeDisabled",
    "HandoffArtefact",
    "HandoffEvent",
    "SpawnResult",
    "SubagentTaskType",
    "TeammateExecutor",
    "TeammateSpawnConfig",
    "handoff_event",
]
