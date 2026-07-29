"""Hook Protocol, events, and result semantics.

Hooks observe and optionally intercept the agent loop. By default they
are observers; blocking is opt-in per `HookSpec.allow_block` so plugin
authors must declare intent before they can veto a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypedDict, runtime_checkable


class HookEvent(StrEnum):
    """Points in the agent loop where hooks fire."""

    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    STOP = "stop"
    NOTIFICATION = "notification"


class PreToolPayload(TypedDict, total=False):
    """PRE_TOOL_USE / SUBAGENT_* hook payload shape."""

    tool_name: str
    tool_input: dict[str, Any]
    subagent_name: str | None
    is_error: bool
    result_summary: str
    mode: str


class StopPayload(TypedDict, total=False):
    """STOP hook payload shape."""

    session_id: str
    phase: str
    verify_nudges: int
    role: str


class UserPromptPayload(TypedDict, total=False):
    """USER_PROMPT_SUBMIT hook payload shape."""

    session_id: str
    prompt: str


@dataclass(frozen=True)
class HookResult:
    """A hook's reply to one event.

    Powers are opt-in via ``HookSpec``:
    - ``blocked=True`` requires ``allow_block`` (else ``hook.blocked.ignored``).
    - ``continue_message`` requires ``allow_continue`` on STOP (else ignored).
    - ``replacement_input`` replaces tool args before execute (PRE_TOOL_USE).
    - ``replacement_result`` replaces tool result text (POST_TOOL_USE).
    - ``inject_context`` appends to the API user message (USER_PROMPT_SUBMIT).
    """

    blocked: bool = False
    feedback: str | None = None
    replacement_input: dict[str, Any] | None = None
    replacement_result: str | None = None
    inject_context: str | None = None
    continue_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookSpec:
    """Declarative metadata for a hook registration."""

    events: tuple[HookEvent, ...]
    priority: int = 0
    allow_block: bool = False
    allow_continue: bool = False


@runtime_checkable
class Hook(Protocol):
    """An observer / interceptor of agent-loop events."""

    spec: HookSpec

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult: ...
