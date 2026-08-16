"""Durable / recoverable harness state (checkpoints, shadow FS snaps)."""

from __future__ import annotations

from dream.state.shadow import (
    CheckpointOutcome,
    CheckpointReason,
    CheckpointSnapshot,
    CombinedRestoreResult,
    MutatingToolName,
    RestoreOutcome,
    RestoreResult,
    ShadowCheckpointConfig,
    ShadowCheckpointHook,
    ShadowCheckpointManager,
    ShadowCheckpointStore,
    rewind_transcript,
)

__all__ = [
    "CheckpointOutcome",
    "CheckpointReason",
    "CheckpointSnapshot",
    "CombinedRestoreResult",
    "MutatingToolName",
    "RestoreOutcome",
    "RestoreResult",
    "ShadowCheckpointConfig",
    "ShadowCheckpointHook",
    "ShadowCheckpointManager",
    "ShadowCheckpointStore",
    "rewind_transcript",
]
