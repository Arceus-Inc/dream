"""Durable session save/resume — FileSessionStore + Session snapshot/restore."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from dream.errors import SessionResumeError
from dream.harness import Harness, HarnessConfig
from dream.services.session_store import FileSessionStore
from dream.session import Session, SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _engine(streamer: FakeStreamer, dispatcher: FakeDispatcher) -> QueryEngine:
    return QueryEngine(
        streamer=streamer,
        dispatcher=dispatcher,
        session_id="s_test",
        working_dir=Path("/tmp"),
        max_turns=4,
    )


async def _collect(session: Session, prompt: str) -> None:
    async for _ in session.send(prompt):
        pass


def _session_with_tool_transcript() -> Session:
    session = Session(
        id="abc123",
        options=SessionOptions(model="gpt-test", system_prompt="be helpful"),
    )
    session._transcript = [
        ConversationMessage(role="user", content=[TextBlock(text="use tool")]),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="calling"),
                ToolUseBlock(id="tu_1", name="echo", input={"x": 1}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="tu_1", content="done", is_error=False)],
        ),
        ConversationMessage(role="assistant", content=[TextBlock(text="wrap")]),
    ]
    session.cost.input_tokens = 10
    session.cost.output_tokens = 5
    session.cost.cache_read_tokens = 2
    session.cost.cache_write_tokens = 1
    session.cost.cost_usd = 0.42
    return session


# --- FileSessionStore roundtrip ------------------------------------------------


def test_save_load_roundtrip_preserves_messages_tool_calls_and_cost(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    session = _session_with_tool_transcript()
    snapshot = session.snapshot()

    path = store.save(snapshot)
    assert path == store.path_for("abc123")
    assert path.exists()

    loaded = store.load("abc123")
    assert loaded.schema_version == 1
    assert loaded.session_id == "abc123"
    assert loaded.model == "gpt-test"
    assert loaded.system_prompt == "be helpful"
    assert loaded.cost == snapshot.cost
    assert len(loaded.messages) == 4
    assert loaded.tool_calls == snapshot.tool_calls
    # saved_at is set at snapshot time; reload preserves it.
    assert loaded.saved_at == snapshot.saved_at
    assert path.stat().st_mode & 0o777 == 0o600


def test_snapshot_persists_serializable_options_only() -> None:
    options = SessionOptions(
        model="m1",
        system_prompt="sys",
        max_turns=3,
        metadata={"trace_id": "abc", "opaque": object()},
    )
    snapshot = Session(id="s1", options=options).snapshot()

    assert snapshot.max_turns == 3
    assert snapshot.metadata == {"trace_id": "abc"}


def test_snapshot_persists_engine_max_turns_when_option_unset() -> None:
    session = Session(
        id="s1",
        options=SessionOptions(model="m1"),
        _engine=_engine(FakeStreamer([]), FakeDispatcher()),
    )
    session._engine.max_turns = 6  # type: ignore[attr-defined]

    assert session.snapshot().max_turns == 6


async def test_snapshot_rejects_while_send_in_flight() -> None:
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["hi"], delay=0.2, usage=UsageSnapshot())]
    )
    session = Session(
        id="s1",
        _engine=_engine(streamer, FakeDispatcher()),
    )

    send_task = asyncio.create_task(_collect(session, "prompt"))
    await asyncio.sleep(0.01)
    with pytest.raises(RuntimeError, match="in flight"):
        session.snapshot()
    await send_task


def test_path_traversal_session_id_rejected(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    with pytest.raises(ValueError, match="unsafe"):
        store.path_for("../escape")
    with pytest.raises(ValueError, match="unsafe"):
        store.path_for("foo/bar")


def test_load_missing_raises_typed_resume_error(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    with pytest.raises(SessionResumeError) as excinfo:
        store.load("missing-session")
    assert excinfo.value.reason == "missing"
    assert excinfo.value.session_id == "missing-session"
    assert excinfo.value.should_clear_handle is True


def test_load_unparseable_file_reports_corrupt(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    store.path_for("s1").write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionResumeError) as excinfo:
        store.load("s1")
    assert excinfo.value.reason == "corrupt"


def test_load_future_schema_reports_mismatch(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    store.path_for("s1").write_text('{"schema_version": 99}', encoding="utf-8")
    with pytest.raises(SessionResumeError) as excinfo:
        store.load("s1")
    assert excinfo.value.reason == "schema_mismatch"


def test_load_truncated_payload_reports_corrupt(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    store.path_for("s1").write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(SessionResumeError) as excinfo:
        store.load("s1")
    assert excinfo.value.reason == "corrupt"


def test_list_and_delete_sessions(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    assert store.list_sessions() == []

    store.save(_session_with_tool_transcript().snapshot())
    assert store.list_sessions() == ["abc123"]

    assert store.delete("abc123") is True
    assert store.delete("abc123") is False
    assert store.list_sessions() == []


# --- Session.snapshot / restore_from_snapshot ----------------------------------


def test_snapshot_extracts_tool_call_records_from_transcript() -> None:
    session = Session(id="s1", options=SessionOptions(model="m1", system_prompt="sys"))
    session._transcript = _session_with_tool_transcript()._transcript
    session.cost.input_tokens = 7
    session.cost.output_tokens = 3

    snap = session.snapshot()

    assert snap.session_id == "s1"
    assert snap.model == "m1"
    assert snap.system_prompt == "sys"
    assert snap.cost.input_tokens == 7
    assert snap.cost.output_tokens == 3
    assert len(snap.messages) == 4
    assert len(snap.tool_calls) == 1
    rec = snap.tool_calls[0]
    assert rec.tool_use_id == "tu_1"
    assert rec.tool_name == "echo"
    assert rec.input == {"x": 1}
    assert rec.result_content == "done"
    assert rec.is_error is False


def test_restore_from_snapshot_replaces_transcript_and_cost() -> None:
    session = Session(id="s1")
    session._transcript = [ConversationMessage(role="user", content=[TextBlock(text="old")])]
    session.cost.input_tokens = 99

    source = _session_with_tool_transcript()
    snap = source.snapshot()

    session.restore_from_snapshot(snap)

    assert len(session._transcript) == 4
    assert session._transcript[0].text == "use tool"
    assert session.cost.input_tokens == 10
    assert session.cost.output_tokens == 5


async def test_restore_then_send_sees_resumed_transcript() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["next"], usage=UsageSnapshot(input_tokens=1)),
        ]
    )
    session = Session(
        id="s1",
        _engine=_engine(streamer, FakeDispatcher()),
    )
    source = _session_with_tool_transcript()
    session.restore_from_snapshot(source.snapshot())

    await _collect(session, "continue")

    # First streamer call must include prior history + new prompt.
    assert len(streamer.calls) == 1
    resumed = streamer.calls[0]
    assert resumed[-1].text == "continue"
    assert resumed[-2].text == "wrap"
    assert any(m.tool_uses for m in resumed if m.role == "assistant")


# --- Harness save_session / resume_session -------------------------------------


async def test_harness_save_and_resume_session_roundtrip(tmp_path: Path) -> None:
    tool_use = ToolUseBlock(id="tu_1", name="echo", input={"k": "v"})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["calling"],
                tool_uses=[tool_use],
                usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            ),
            FakeTurn(
                text_chunks=["done"],
                usage=UsageSnapshot(input_tokens=4, output_tokens=2),
            ),
            FakeTurn(text_chunks=["after-resume"]),
        ]
    )
    dispatcher = FakeDispatcher(results={"echo": ("result-content", False)})

    def factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=dispatcher,
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=4,
        )

    paths = DreamPaths.resolve(tmp_path, home=tmp_path / "home")
    store = FileSessionStore(paths.sessions_dir)
    config = HarnessConfig(
        working_dir=Path("/tmp"),
        paths=paths,
        session_store=store,
        _engine_factory=factory,  # type: ignore[call-arg]
    )
    harness = Harness(config)

    session = await harness.start_session(SessionOptions(model="test-model"))
    await _collect(session, "use tool")

    handle = await harness.save_session(session)
    assert handle.path.exists()
    assert handle.session_id == session.id
    assert handle.usage_total.input_tokens == session.cost.input_tokens

    resumed = await harness.resume_session(session.id)
    assert resumed.id == session.id
    assert len(resumed.transcript) == len(session.transcript)
    assert resumed.cost.input_tokens == session.cost.input_tokens
    assert resumed.cost.output_tokens == session.cost.output_tokens

    # Resume must bind a fresh engine; next send continues the thread.
    await _collect(resumed, "pick up")
    assert len(streamer.calls) == 3
    third_call = streamer.calls[2]
    assert third_call[-1].text == "pick up"


async def test_resume_honors_persisted_max_turns_over_harness_default(
    tmp_path: Path,
) -> None:
    observed: list[int | None] = []

    def factory(session_id: str, options: SessionOptions) -> QueryEngine:
        observed.append(options.max_turns)
        return QueryEngine(
            streamer=FakeStreamer([]),
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 99,
        )

    paths = DreamPaths.resolve(tmp_path, home=tmp_path / "home")
    store = FileSessionStore(paths.sessions_dir)
    harness = Harness(
        HarnessConfig(
            working_dir=Path("/tmp"),
            paths=paths,
            session_store=store,
            _engine_factory=factory,  # type: ignore[call-arg]
        )
    )

    session = await harness.start_session(SessionOptions(model="test-model"))
    session._engine.max_turns = 6  # type: ignore[attr-defined]
    session._transcript = [
        ConversationMessage(role="user", content=[TextBlock(text="hello")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="hi")]),
    ]
    await harness.save_session(session)

    await harness.resume_session(session.id)

    assert observed == [None, 6]


def _handle_harness(
    tmp_path: Path,
    *,
    working_dir: Path,
    streamer: FakeStreamer | None = None,
) -> Harness:
    """Harness wired to a temp session store, engines rooted at ``working_dir``."""
    shared = streamer if streamer is not None else FakeStreamer([])

    def factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=shared,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=working_dir,
            max_turns=4,
        )

    paths = DreamPaths.resolve(tmp_path, home=tmp_path / "home")
    return Harness(
        HarnessConfig(
            working_dir=working_dir,
            paths=paths,
            session_store=FileSessionStore(paths.sessions_dir),
            _engine_factory=factory,  # type: ignore[call-arg]
        )
    )


async def test_start_session_accepts_caller_supplied_id(tmp_path: Path) -> None:
    harness = _handle_harness(tmp_path, working_dir=tmp_path)

    session = await harness.start_session(session_id="beat-t123-emp7")

    assert session.id == "beat-t123-emp7"
    handle = await harness.save_session(session)
    assert handle.session_id == "beat-t123-emp7"
    assert handle.path.name == "beat-t123-emp7.json"


async def test_start_session_rejects_traversal_in_supplied_id(tmp_path: Path) -> None:
    harness = _handle_harness(tmp_path, working_dir=tmp_path)

    with pytest.raises(ValueError, match="unsafe"):
        await harness.start_session(session_id="../escape")


async def test_handle_usage_delta_covers_only_work_since_previous_save(
    tmp_path: Path,
) -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["one"], usage=UsageSnapshot(input_tokens=5, output_tokens=2)),
            FakeTurn(text_chunks=["two"], usage=UsageSnapshot(input_tokens=3, output_tokens=1)),
        ]
    )
    harness = _handle_harness(tmp_path, working_dir=tmp_path, streamer=streamer)
    session = await harness.start_session(session_id="s1")

    await _collect(session, "first")
    first = await harness.save_session(session)
    assert first.usage_delta.input_tokens == 5
    assert first.usage_total.input_tokens == 5

    await _collect(session, "second")
    second = await harness.save_session(session)
    assert second.usage_delta.input_tokens == 3
    assert second.usage_total.input_tokens == 8


async def test_resumed_session_reports_delta_from_restored_total(tmp_path: Path) -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["one"], usage=UsageSnapshot(input_tokens=5)),
            FakeTurn(text_chunks=["after"], usage=UsageSnapshot(input_tokens=4)),
        ]
    )
    harness = _handle_harness(tmp_path, working_dir=tmp_path, streamer=streamer)
    session = await harness.start_session(session_id="s1")
    await _collect(session, "first")
    await harness.save_session(session)

    resumed = await harness.resume_session("s1")
    await _collect(resumed, "second")
    handle = await harness.save_session(resumed)

    assert handle.usage_delta.input_tokens == 4
    assert handle.usage_total.input_tokens == 9


async def test_resume_rejects_working_dir_change(tmp_path: Path) -> None:
    origin = tmp_path / "repo-a"
    origin.mkdir()
    harness = _handle_harness(tmp_path, working_dir=origin)
    session = await harness.start_session(session_id="s1")
    await harness.save_session(session)

    elsewhere = tmp_path / "repo-b"
    elsewhere.mkdir()
    moved = _handle_harness(tmp_path, working_dir=elsewhere)

    with pytest.raises(SessionResumeError) as excinfo:
        await moved.resume_session("s1")
    assert excinfo.value.reason == "working_dir_mismatch"
    # The snapshot itself is intact, so the caller keeps its handle.
    assert excinfo.value.should_clear_handle is False

    opted_in = await moved.resume_session("s1", allow_working_dir_change=True)
    assert opted_in.id == "s1"


async def test_reset_session_clears_snapshot(tmp_path: Path) -> None:
    harness = _handle_harness(tmp_path, working_dir=tmp_path)
    session = await harness.start_session(session_id="s1")
    await harness.save_session(session)

    assert await harness.reset_session("s1") is True
    assert await harness.reset_session("s1") is False

    with pytest.raises(SessionResumeError) as excinfo:
        await harness.resume_session("s1")
    assert excinfo.value.reason == "missing"


async def test_snapshot_records_engine_working_dir(tmp_path: Path) -> None:
    harness = _handle_harness(tmp_path, working_dir=tmp_path)
    session = await harness.start_session(session_id="s1")

    assert session.snapshot().working_dir == str(tmp_path)


def test_sanitize_after_restore_leaves_no_dangling_tool_use() -> None:
    session = Session(id="s1")
    source = _session_with_tool_transcript()
    session.restore_from_snapshot(source.snapshot())
    sanitized = sanitize_conversation_messages(session.transcript)
    tool_use_ids = {
        block.id for msg in sanitized for block in msg.content if isinstance(block, ToolUseBlock)
    }
    tool_result_ids = {
        block.tool_use_id
        for msg in sanitized
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    }
    assert tool_use_ids == tool_result_ids
