"""Spec 03 stage 2 — internal ``StreamEvent`` union shape.

The engine's act-loop yields typed events instead of raw provider output so
the runner can drive the FSM, fire hooks, accumulate cost, and update the UI
**without ever parsing assistant prose** (acceptance criterion #7).

This module pins the constructors and frozen semantics of every event the
loop is allowed to emit. The taxonomy is closed — adding a new variant is a
public-API decision even though the module itself is internal.

Note: this internal ``StreamEvent`` is *not* the cross-repo
``ProviderEvent`` (``dream.contracts.provider``). The provider Protocol
returns ``{type: str, data: dict}`` events for substrate flexibility; the
engine translates those into typed ``StreamEvent``s here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactionDoneEvent,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from dream.engine._messages import ContentBlock, TextBlock, ToolUseBlock


def test_assistant_text_delta_holds_text() -> None:
    ev = AssistantTextDelta(text="hello")
    assert ev.text == "hello"


def test_assistant_turn_complete_carries_blocks_and_usage() -> None:
    blocks: list[ContentBlock] = [TextBlock(text="done")]
    usage = UsageSnapshot(input_tokens=10, output_tokens=5)
    ev = AssistantTurnComplete(blocks=blocks, usage=usage)
    assert ev.blocks == blocks
    assert ev.usage == usage


def test_tool_execution_started_holds_tool_id_input() -> None:
    ev = ToolExecutionStarted(tool="read", id="t1", input={"path": "/x"})
    assert ev.tool == "read"
    assert ev.id == "t1"
    assert ev.input == {"path": "/x"}


def test_tool_execution_completed_defaults_is_error_false() -> None:
    ev = ToolExecutionCompleted(tool="read", id="t1", result="contents")
    assert ev.tool == "read"
    assert ev.id == "t1"
    assert ev.result == "contents"
    assert ev.is_error is False


def test_tool_execution_completed_carries_is_error_true() -> None:
    ev = ToolExecutionCompleted(tool="read", id="t1", result="boom", is_error=True)
    assert ev.is_error is True


def test_status_event_holds_message() -> None:
    ev = StatusEvent(message="reconnecting")
    assert ev.message == "reconnecting"


def test_compact_progress_event_holds_progress() -> None:
    ev = CompactProgressEvent(pct=0.5, message="compacting")
    assert ev.pct == 0.5
    assert ev.message == "compacting"


def test_compact_progress_event_message_defaults_empty() -> None:
    ev = CompactProgressEvent(pct=0.25)
    assert ev.message == ""


def test_error_event_holds_message_and_default_recoverable() -> None:
    ev = ErrorEvent(message="boom")
    assert ev.message == "boom"
    assert ev.recoverable is False


def test_error_event_can_be_recoverable() -> None:
    ev = ErrorEvent(message="rate limit", recoverable=True)
    assert ev.recoverable is True


@pytest.mark.parametrize(
    "ev",
    [
        AssistantTextDelta(text="x"),
        AssistantTurnComplete(blocks=[TextBlock(text="x")], usage=UsageSnapshot()),
        ToolExecutionStarted(tool="t", id="1", input={}),
        ToolExecutionCompleted(tool="t", id="1", result="r"),
        StatusEvent(message="m"),
        CompactProgressEvent(pct=0.0),
        CompactionDoneEvent(
            tier="microcompact",
            removed_messages=0,
            freed_tokens=0,
            resulting_utilisation=0.0,
        ),
        ErrorEvent(message="m"),
    ],
)
def test_events_are_frozen(ev: object) -> None:
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(ev, "_invalid_attr_unique_X", 1)


def test_stream_event_union_members_are_assignable() -> None:
    """Every event variant satisfies the StreamEvent union.

    Pinned as runtime asserts via isinstance against the union to ensure
    the alias stays exhaustive — adding a new event variant without
    updating the union will be caught here.
    """
    variants: list[StreamEvent] = [
        AssistantTextDelta(text="x"),
        AssistantTurnComplete(
            blocks=[ToolUseBlock(id="i", name="n", input={})], usage=UsageSnapshot()
        ),
        ToolExecutionStarted(tool="t", id="1", input={}),
        ToolExecutionCompleted(tool="t", id="1", result="r"),
        StatusEvent(message="m"),
        CompactProgressEvent(pct=0.5),
        CompactionDoneEvent(
            tier="microcompact",
            removed_messages=2,
            freed_tokens=100,
            resulting_utilisation=0.42,
        ),
        ErrorEvent(message="m"),
    ]
    members = get_args(StreamEvent)
    # Each constructed variant really is a member of the union...
    for v in variants:
        assert isinstance(v, members), f"{type(v).__name__} is not a StreamEvent member"
    # ...and every union member is represented above, so adding a variant to the
    # union without testing it (or dropping one) fails here too.
    assert {type(v) for v in variants} == set(members)


def test_event_types_are_distinct() -> None:
    """Variants don't collapse onto each other via duck-typing."""
    types = {
        type(AssistantTextDelta(text="x")),
        type(AssistantTurnComplete(blocks=[], usage=UsageSnapshot())),
        type(ToolExecutionStarted(tool="t", id="1", input={})),
        type(ToolExecutionCompleted(tool="t", id="1", result="r")),
        type(StatusEvent(message="m")),
        type(CompactProgressEvent(pct=0.0)),
        type(
            CompactionDoneEvent(
                tier="microcompact",
                removed_messages=0,
                freed_tokens=0,
                resulting_utilisation=0.0,
            )
        ),
        type(ErrorEvent(message="m")),
    }
    assert len(types) == 8
