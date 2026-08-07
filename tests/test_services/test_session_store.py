"""Durable session save/resume — FileSessionStore + Session snapshot/restore."""

from __future__ import annotations

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


def test_path_traversal_session_id_rejected(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    with pytest.raises(ValueError, match="unsafe"):
        store.path_for("../escape")
    with pytest.raises(ValueError, match="unsafe"):
        store.path_for("foo/bar")


def test_load_missing_raises_file_not_found(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("missing-session")


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
        paths=paths,
        session_store=store,
        _engine_factory=factory,  # type: ignore[call-arg]
    )
    harness = Harness(config)

    session = await harness.start_session(SessionOptions(model="test-model"))
    await _collect(session, "use tool")

    saved_path = await harness.save_session(session)
    assert saved_path.exists()

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
