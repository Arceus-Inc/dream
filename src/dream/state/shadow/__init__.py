"""Hermes-style shadow-git filesystem checkpoints (pre-mutate)."""

from __future__ import annotations

from dream.state.shadow._hook import ShadowCheckpointHook
from dream.state.shadow._manager import ShadowCheckpointManager
from dream.state.shadow._store import ShadowCheckpointStore
from dream.state.shadow._types import (
    CheckpointOutcome,
    CheckpointReason,
    CheckpointSnapshot,
    EnsureResult,
    MutatingToolName,
    RestoreOutcome,
    RestoreResult,
    ShadowCheckpointConfig,
)

__all__ = [
    "CheckpointOutcome",
    "CheckpointReason",
    "CheckpointSnapshot",
    "EnsureResult",
    "MutatingToolName",
    "RestoreOutcome",
    "RestoreResult",
    "ShadowCheckpointConfig",
    "ShadowCheckpointHook",
    "ShadowCheckpointManager",
    "ShadowCheckpointStore",
]
