"""Hook Protocol, events, and result semantics.

Hooks observe and optionally intercept the agent loop. By default they
are observers; blocking is opt-in per `HookSpec.allow_block` so plugin
authors must declare intent before they can veto a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class HookEvent(StrEnum):
    """Points in the agent loop where hooks fire."""

    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    SUBAGENT_STOP = "subagent_stop"
    STOP = "stop"
    NOTIFICATION = "notification"


@dataclass(frozen=True)
class HookResult:
    """A hook's reply to one event.

    `blocked=True` requires the hook's `HookSpec.allow_block` to be set,
    otherwise the executor ignores the block flag and emits a warning
    event. `replacement_input`, when present, replaces the tool input
    before execution (pre-tool only).
    """

    blocked: bool = False
    feedback: str | None = None
    replacement_input: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookSpec:
    """Declarative metadata for a hook registration."""

    events: tuple[HookEvent, ...]
    priority: int = 0
    allow_block: bool = False


@runtime_checkable
class Hook(Protocol):
    """An observer / interceptor of agent-loop events."""

    spec: HookSpec

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult: ...
