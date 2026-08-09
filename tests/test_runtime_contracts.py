"""Public control-plane views over durable sessions and traces."""

from __future__ import annotations

from collections import UserDict
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dream import FileSessionStore, RunTrace, Session, SessionOptions
from dream._immutable_json import FrozenJsonArray, FrozenJsonObject
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
    assert tool_use.input["query"] == "paperclip"
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


def test_session_snapshot_deeply_captures_nested_json(tmp_path: Path) -> None:
    tags = ["before"]
    options = SessionOptions(metadata=UserDict(context=UserDict(tags=tags)))
    session = Session(id="session-1", options=options)
    session.transcript.append(
        ConversationMessage(
            role="user",
            content=[ToolUseBlock(id="call-1", name="search", input=UserDict(tags=tags))],
        )
    )

    snapshot = session.snapshot()
    tags.append("after")

    context = snapshot.metadata["context"]
    assert isinstance(context, FrozenJsonObject)
    frozen_tags = context["tags"]
    assert isinstance(frozen_tags, FrozenJsonArray)
    assert frozen_tags.values == ("before",)
    tool_input = snapshot.tool_calls[0].input["tags"]
    assert isinstance(tool_input, FrozenJsonArray)
    assert tool_input.values == ("before",)

    store = FileSessionStore(tmp_path)
    store.save(snapshot)
    loaded = store.load(snapshot.session_id)
    assert loaded.metadata == snapshot.metadata
    assert loaded.tool_calls == snapshot.tool_calls


def test_run_trace_deeply_freezes_event_attributes(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        '{"ts":"2026-08-09T00:00:00+00:00","session_id":"session-1",'
        '"task_id":null,"event_type":"state.transition","span_id":"span-1",'
        '"parent_span_id":null,"attributes":{"context":{"tags":["before"]}}}\n',
        encoding="utf-8",
    )

    trace = RunTrace.read(session_id="session-1", path=path)

    attributes = trace.events[0].attributes
    assert isinstance(attributes, FrozenJsonObject)
    context = attributes["context"]
    assert isinstance(context, FrozenJsonObject)
    tags = context["tags"]
    assert isinstance(tags, FrozenJsonArray)
    assert tags.values == ("before",)
