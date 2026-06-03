"""Public typed event stream.

Everything a `Session` yields is one of these dataclasses. Consumers
pattern-match on type. The set is intentionally small; richer details go
in the per-event payload fields rather than new event classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class TextDelta:
    """Streamed text from the model."""

    text: str


@dataclass(frozen=True)
class ToolUseStart:
    """Model decided to invoke a tool."""

    tool_use_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolUseResult:
    """Tool execution finished."""

    tool_use_id: str
    name: str
    content: str
    is_error: bool = False
    structured: dict[str, Any] | None = None


@dataclass(frozen=True)
class TurnComplete:
    """A single assistant turn finished."""

    stop_reason: str
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Compacted:
    """History was compacted between turns."""

    removed_messages: int
    summary_tokens: int


@dataclass(frozen=True)
class HookBlocked:
    """A hook with `allow_block=True` vetoed an action."""

    event: str
    hook_name: str
    feedback: str | None = None


@dataclass(frozen=True)
class PermissionDenied:
    """The permission checker denied an action."""

    action: str
    reason: str


@dataclass(frozen=True)
class Error:
    """A recoverable error surfaced to the consumer."""

    code: str
    message: str


Event = Union[
    TextDelta,
    ToolUseStart,
    ToolUseResult,
    TurnComplete,
    Compacted,
    HookBlocked,
    PermissionDenied,
    Error,
]


__all__ = [
    "Compacted",
    "Error",
    "Event",
    "HookBlocked",
    "PermissionDenied",
    "TextDelta",
    "ToolUseStart",
    "ToolUseResult",
    "TurnComplete",
]
