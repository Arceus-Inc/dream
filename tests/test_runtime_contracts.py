"""Public control-plane views over durable sessions and traces."""

from __future__ import annotations

from collections import UserDict
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dream import RunTrace, Session
from dream.engine._messages import ConversationMessage, TextBlock, ToolUseBlock
from dream.observability import TraceEvent, TraceWriter


def test_session_snapshot_is_an_immutable_point_in_time_view() -> None:
    session = Session(id="session-1")
    before = session.snapshot()
    session.transcript.append(
        ConversationMessage(
            role="user",
            content=[
                TextBlock(text="continue"),
                ToolUseBlock(id="call-1", name="search", input=UserDict(query="paperclip")),
            ],
        )
    )
    after = session.snapshot()

    assert before.messages == ()
    assert after.messages[0].role == "user"
    assert isinstance(after.messages, tuple)
    assert isinstance(after.messages[0].content, tuple)
    tool_use = after.messages[0].content[1]
    assert tool_use.kind == "tool_use"
    assert tool_use.input == (("query", "paperclip"),)
    assert isinstance(after.tool_calls, tuple)
    with pytest.raises(FrozenInstanceError):
        after.session_id = "other"  # type: ignore[misc]


def test_run_trace_reads_existing_jsonl_events_as_an_immutable_aggregate(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    event = TraceEvent(
        ts="2026-08-09T00:00:00+00:00",
        session_id="session-1",
        task_id="task-1",
        event_type="tool.result",
        span_id="span-1",
        parent_span_id=None,
        attributes=UserDict(),
    )
    TraceWriter(path).write(event)
    path.write_text(path.read_text(encoding="utf-8") + "{truncated\n", encoding="utf-8")

    trace = RunTrace.read(session_id="session-1", path=path)

    assert trace.events == (event,)
    assert isinstance(trace.events, tuple)
    with pytest.raises(FrozenInstanceError):
        trace.session_id = "other"  # type: ignore[misc]


def test_run_trace_rejects_events_for_another_session(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    TraceWriter(path).write(
        TraceEvent(
            ts="2026-08-09T00:00:00+00:00",
            session_id="other-session",
            task_id=None,
            event_type="state.transition",
            span_id="span-1",
            parent_span_id=None,
            attributes=UserDict(),
        )
    )

    with pytest.raises(ValueError, match="other-session"):
        RunTrace.read(session_id="session-1", path=path)
