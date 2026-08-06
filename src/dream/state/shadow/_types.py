"""Typed contracts for shadow filesystem checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path


class MutatingToolName(StrEnum):
    """Built-in tools that may mutate the working tree."""

    WRITE_FILE = "write_file"
    EDIT_FILE = "edit_file"
    BASH = "bash"
    EXECUTE_CODE = "execute_code"
    TASK_CREATE = "task_create"


class CheckpointReason(StrEnum):
    """Why a snapshot was requested (stored as the git commit subject)."""

    BEFORE_WRITE_FILE = "before write_file"
    BEFORE_EDIT_FILE = "before edit_file"
    BEFORE_BASH = "before bash"
    BEFORE_EXECUTE_CODE = "before execute_code"
    BEFORE_TASK_CREATE = "before task_create"
    BEFORE_MUTATING_TOOL = "before mutating tool"
    PRE_ROLLBACK = "pre-rollback"


class CheckpointOutcome(Enum):
    """Result of :meth:`ShadowCheckpointManager.ensure`."""

    TAKEN = "taken"
    DISABLED = "disabled"
    ALREADY_THIS_TURN = "already_this_turn"
    NO_CHANGES = "no_changes"
    DIRECTORY_TOO_BROAD = "directory_too_broad"
    GIT_UNAVAILABLE = "git_unavailable"
    FAILED = "failed"


class RestoreOutcome(Enum):
    """Result of :meth:`ShadowCheckpointManager.restore`."""

    RESTORED = "restored"
    DISABLED = "disabled"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ShadowCheckpointConfig:
    """Manager knobs (Hermes CheckpointManager subset)."""

    enabled: bool = True
    max_snapshots: int = 20
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot:
    """One retained shadow commit for a working directory."""

    commit_sha: str
    short_sha: str
    reason: CheckpointReason | str
    working_dir: Path


@dataclass(frozen=True, slots=True)
class EnsureResult:
    """Typed ensure outcome — never a bare bool or dict."""

    outcome: CheckpointOutcome
    snapshot: CheckpointSnapshot | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Typed restore outcome."""

    outcome: RestoreOutcome
    restored_to: str = ""
    detail: str = ""


_TOOL_TO_REASON: dict[MutatingToolName, CheckpointReason] = {
    MutatingToolName.WRITE_FILE: CheckpointReason.BEFORE_WRITE_FILE,
    MutatingToolName.EDIT_FILE: CheckpointReason.BEFORE_EDIT_FILE,
    MutatingToolName.BASH: CheckpointReason.BEFORE_BASH,
    MutatingToolName.EXECUTE_CODE: CheckpointReason.BEFORE_EXECUTE_CODE,
    MutatingToolName.TASK_CREATE: CheckpointReason.BEFORE_TASK_CREATE,
}


def reason_for_tool(tool_name: str) -> CheckpointReason | None:
    """Map a tool name to a checkpoint reason, or ``None`` if non-mutating."""
    try:
        tool = MutatingToolName(tool_name)
    except ValueError:
        return None
    return _TOOL_TO_REASON[tool]


__all__ = [
    "CheckpointOutcome",
    "CheckpointReason",
    "CheckpointSnapshot",
    "EnsureResult",
    "MutatingToolName",
    "RestoreOutcome",
    "RestoreResult",
    "ShadowCheckpointConfig",
    "reason_for_tool",
]
