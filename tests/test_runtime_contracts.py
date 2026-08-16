"""Public control-plane views over durable sessions and traces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dream import (
    FileSessionStore,
    FrozenJsonArray,
    FrozenJsonObject,
    RunTrace,
    Session,
    SessionOptions,
    freeze_json_value,
    thaw_json_value,
)
from dream.engine._messages import ConversationMessage, TextBlock, ToolUseBlock
from dream.harness import Harness, HarnessConfig
from dream.observability import TraceEvent, TraceWriter
from dream.services.session_store import is_json_value


def test_session_snapshot_is_an_immutable_point_in_time_view() -> None:
    session = Session(id="session-1")
    before = session.snapshot()
    session.transcript.append(
        ConversationMessage(
            role="user",
            content=[
                TextBlock(text="continue"),
                ToolUseBlock(id="call-1", name="search", input={"query": "paperclip"}),
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
        attributes=FrozenJsonObject.capture({"tool.name": "search"}),
    )
    TraceWriter(path).write(event)
    path.write_text(path.read_text(encoding="utf-8") + "{truncated\n", encoding="utf-8")

    trace = RunTrace.read(session_id="session-1", path=path)

    assert trace.session_id == "session-1"
    assert len(trace.events) == 1
    read = trace.events[0]
    assert read.ts == event.ts
    assert read.event_type == event.event_type
    assert read.attributes["tool.name"] == "search"
    assert isinstance(trace.events, tuple)
    assert isinstance(read.attributes, FrozenJsonObject)
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
            attributes=FrozenJsonObject(),
        )
    )

    with pytest.raises(ValueError, match="other-session"):
        RunTrace.read(session_id="session-1", path=path)


def test_session_snapshot_deeply_captures_nested_json(tmp_path: Path) -> None:
    tags = ["before"]
    options = SessionOptions(metadata={"context": {"tags": tags}})
    session = Session(id="session-1", options=options)
    session.transcript.append(
        ConversationMessage(
            role="user",
            content=[ToolUseBlock(id="call-1", name="search", input={"tags": tags})],
        )
    )

    snapshot = session.snapshot()
    tags.append("after")

    context = snapshot.metadata["context"]
    assert isinstance(context, FrozenJsonObject)
    frozen_tags = context["tags"]
    assert isinstance(frozen_tags, FrozenJsonArray)
    assert frozen_tags == ["before"]
    tool_input = snapshot.tool_calls[0].input["tags"]
    assert isinstance(tool_input, FrozenJsonArray)
    assert tool_input == ["before"]

    store = FileSessionStore(tmp_path)
    store.save(snapshot)
    loaded = store.load(snapshot.session_id)
    assert loaded.metadata == snapshot.metadata
    assert loaded.tool_calls == snapshot.tool_calls
    assert isinstance(loaded.messages, tuple)
    assert isinstance(loaded.metadata, FrozenJsonObject)


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
    assert tags == ["before"]
    with pytest.raises(TypeError):
        attributes["context"] = {"tags": ["mutated"]}  # type: ignore[index]


async def test_resume_thaws_snapshot_metadata_into_live_session_options(tmp_path: Path) -> None:
    """Live SessionOptions stay mutable; only the captured snapshot is frozen."""
    options = SessionOptions(metadata={"context": {"tags": ["before"]}})
    session = Session(id="session-1", options=options)
    snapshot = session.snapshot()
    store = FileSessionStore(tmp_path)
    store.save(snapshot)

    harness = Harness(HarnessConfig(working_dir=tmp_path, session_store=store))
    resumed = await harness.resume_session("session-1", allow_working_dir_change=True)

    live_context = resumed.options.metadata["context"]
    assert live_context == {"tags": ["before"]}
    assert isinstance(resumed.options.metadata, dict)
    live_context["tags"].append("after")

    frozen_context = snapshot.metadata["context"]
    assert isinstance(frozen_context, FrozenJsonObject)
    frozen_tags = frozen_context["tags"]
    assert isinstance(frozen_tags, FrozenJsonArray)
    assert frozen_tags == ["before"]


def test_frozen_json_constructors_seal_nested_values() -> None:
    sealed_object = FrozenJsonObject((("context", {"tags": ["before"]}),))
    sealed_array = FrozenJsonArray(({"query": "paperclip"},))

    context = sealed_object["context"]
    assert isinstance(context, FrozenJsonObject)
    tags = context["tags"]
    assert isinstance(tags, FrozenJsonArray)
    assert tags[0] == "before"
    nested = sealed_array[0]
    assert isinstance(nested, FrozenJsonObject)
    assert nested["query"] == "paperclip"
    with pytest.raises(ValueError, match="not JSON-compatible"):
        FrozenJsonObject((("bad", object()),))


def test_frozen_json_object_equality_is_order_insensitive() -> None:
    left = FrozenJsonObject.capture({"b": 2, "a": 1})
    right = FrozenJsonObject((("a", 1), ("b", 2)))
    assert left == right
    assert hash(left) == hash(right)
    assert left == {"a": 1, "b": 2}


def test_frozen_json_array_is_a_sequence() -> None:
    frozen = FrozenJsonArray.capture(["before", "after"])
    assert isinstance(frozen, Sequence)
    assert frozen[0] == "before"
    assert frozen[:1] == FrozenJsonArray.capture(["before"])
    assert list(frozen) == ["before", "after"]
    assert thaw_json_value(freeze_json_value(["before"])) == ["before"]


def test_trace_event_stores_frozen_attributes() -> None:
    event = TraceEvent(
        ts="2026-08-09T00:00:00+00:00",
        session_id="session-1",
        task_id=None,
        event_type="state.transition",
        span_id="span-1",
        parent_span_id=None,
        attributes={"context": {"tags": ["before"]}},  # type: ignore[arg-type]
    )
    assert isinstance(event.attributes, FrozenJsonObject)
    context = event.attributes["context"]
    assert isinstance(context, FrozenJsonObject)
    assert context["tags"] == ["before"]


def test_from_jsonl_line_tolerates_non_mapping_attributes() -> None:
    from dream.observability._events import from_jsonl_line

    for raw_attrs in ("[]", '"nope"', "null"):
        line = (
            '{"ts":"2026-08-09T00:00:00+00:00","session_id":"session-1",'
            '"task_id":null,"event_type":"state.transition","span_id":"span-1",'
            f'"parent_span_id":null,"attributes":{raw_attrs}}}'
        )
        event = from_jsonl_line(line)
        assert event.attributes == FrozenJsonObject()


def test_is_json_value_recognizes_frozen_values() -> None:
    frozen = FrozenJsonObject.capture({"tags": ["before"]})
    assert is_json_value(frozen) is True
    assert is_json_value(frozen["tags"]) is True
    assert is_json_value(object()) is False
