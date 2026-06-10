"""Typed runtime commands (spec 15 P2 §1).

Each command is a frozen dataclass with a strict, minimal schema. The
action space stays enumerable: adding a command type is a spec change,
not a payload convention.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CancelCommand",
    "Command",
    "StatusCommand",
    "SubmitTaskCommand",
    "WakeCommand",
    "command_from_dict",
]


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class SubmitTaskCommand:
    """Start an end-to-end task (planner → sprint loop) for ``intent``."""

    intent: str
    task_id: str | None = None
    max_sprints: int | None = None
    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.intent or not self.intent.strip():
            raise ValueError("submit_task requires a non-empty intent")
        if self.max_sprints is not None and self.max_sprints < 1:
            raise ValueError("max_sprints must be >= 1 when given")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "submit_task",
            "id": self.id,
            "timestamp": self.timestamp,
            "intent": self.intent,
            "task_id": self.task_id,
            "max_sprints": self.max_sprints,
        }


@dataclass(frozen=True)
class CancelCommand:
    """Cancel a submitted job or stop a running background task."""

    task_id: str
    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("cancel requires a task_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "cancel",
            "id": self.id,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
        }


@dataclass(frozen=True)
class StatusCommand:
    """Ask the runtime what it is doing right now."""

    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "status", "id": self.id, "timestamp": self.timestamp}


@dataclass(frozen=True)
class WakeCommand:
    """Fire one wake cycle now (manual wake source)."""

    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "wake", "id": self.id, "timestamp": self.timestamp}


Command = SubmitTaskCommand | CancelCommand | StatusCommand | WakeCommand


def command_from_dict(data: dict[str, Any]) -> Command:
    """Parse one command file's payload; raise ``ValueError`` on anything off-schema."""
    command_type = data.get("type")
    command_id = str(data.get("id") or _new_id())
    timestamp = float(data.get("timestamp") or _now())
    if command_type == "submit_task":
        max_sprints = data.get("max_sprints")
        return SubmitTaskCommand(
            intent=str(data.get("intent") or ""),
            task_id=data.get("task_id"),
            max_sprints=int(max_sprints) if max_sprints is not None else None,
            id=command_id,
            timestamp=timestamp,
        )
    if command_type == "cancel":
        return CancelCommand(
            task_id=str(data.get("task_id") or ""), id=command_id, timestamp=timestamp
        )
    if command_type == "status":
        return StatusCommand(id=command_id, timestamp=timestamp)
    if command_type == "wake":
        return WakeCommand(id=command_id, timestamp=timestamp)
    raise ValueError(f"unknown command type: {command_type!r}")
