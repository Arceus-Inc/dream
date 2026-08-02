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


class SubagentJoinMode(StrEnum):
    """How the parent joined a spawned child (lifecycle observers)."""

    SYNC = "sync"
    BACKGROUND = "background"


class PreToolPayload(TypedDict, total=False):
    """PRE_TOOL_USE / SUBAGENT_* hook payload shape."""

    tool_name: str
    tool_input: dict[str, Any]
    subagent_name: str | None
    is_error: bool
    result_summary: str
    mode: SubagentJoinMode
    delegation_id: str
    working_dir: str
    structured: object


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
class PreToolHookPayload:
    """PRE_TOOL_USE hook payload — built at dispatch, serialized for hooks."""

    tool_name: str
    tool_input: dict[str, Any]
    subagent_name: str | None = None

    def to_dict(self) -> PreToolPayload:
        payload: PreToolPayload = {
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
        }
        if self.subagent_name is not None:
            payload["subagent_name"] = self.subagent_name
        return payload


@dataclass(frozen=True)
class PostToolHookPayload:
    """POST_TOOL_USE hook payload."""

    tool_name: str
    is_error: bool
    result_summary: str

    def to_dict(self) -> PreToolPayload:
        return {
            "tool_name": self.tool_name,
            "is_error": self.is_error,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class SubagentStartPayload:
    """SUBAGENT_START hook payload (post-PRE effective input)."""

    tool_name: str
    subagent_name: str
    tool_input: dict[str, Any]

    def to_dict(self) -> PreToolPayload:
        return {
            "tool_name": self.tool_name,
            "subagent_name": self.subagent_name,
            "tool_input": self.tool_input,
        }


@dataclass(frozen=True)
class SubagentStopPayload:
    """SUBAGENT_STOP hook payload."""

    tool_name: str
    subagent_name: str
    is_error: bool
    result_summary: str
    mode: SubagentJoinMode = SubagentJoinMode.SYNC
    delegation_id: str | None = None
    working_dir: str | None = None
    structured: object | None = None

    def to_dict(self) -> PreToolPayload:
        payload: PreToolPayload = {
            "tool_name": self.tool_name,
            "subagent_name": self.subagent_name,
            "is_error": self.is_error,
            "result_summary": self.result_summary,
            "mode": self.mode,
        }
        if self.delegation_id is not None:
            payload["delegation_id"] = self.delegation_id
        if self.working_dir is not None:
            payload["working_dir"] = self.working_dir
        if self.structured is not None:
            payload["structured"] = self.structured
        return payload


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
