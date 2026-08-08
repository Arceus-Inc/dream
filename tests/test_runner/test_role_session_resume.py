"""``run_role(session_id=...)`` — a role thread that survives process boundaries.

The seam a control plane needs to run the harness in short windows: name the
session and the role picks up where it left off, exactly like resuming a coding
CLI. What this pins:

- Omitting ``session_id`` keeps the old behaviour — nothing is persisted.
- Naming a session saves a snapshot and returns the handle to resume it, with
  ``usage_delta`` scoped to that run.
- A second call under the same name resumes the transcript rather than
  restarting the conversation.
- An unusable snapshot (corrupt, foreign working directory) never strands the
  role: it starts the thread over under the same name instead of raising.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._events import ErrorEvent, StreamEvent
from dream.engine._messages import ConversationMessage
from dream.harness import Harness, HarnessConfig
from dream.runner import RoleSessionError
from dream.services.session_store import FileSessionStore, TextBlockRecord
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn

WORKING_DIR = Path("/tmp")


def _harness(tmp_path: Path, streamer: FakeStreamer) -> tuple[Harness, FileSessionStore]:
    """Harness whose engines share ``streamer`` and a temp session store."""

    def factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=WORKING_DIR,
            max_turns=options.max_turns or 4,
        )

    paths = DreamPaths.resolve(tmp_path, home=tmp_path / "home")
    store = FileSessionStore(paths.sessions_dir)
    harness = Harness(
        HarnessConfig(
            working_dir=WORKING_DIR,
            paths=paths,
            session_store=store,
            _engine_factory=factory,  # type: ignore[call-arg]
        )
    )
    return harness, store


async def test_run_role_without_session_id_persists_nothing(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    harness, store = _harness(tmp_path, streamer)

    result = await harness.run_role("generator", "do the thing")

    assert result.session_handle is None
    assert store.list_sessions() == []


async def test_run_role_with_session_id_returns_saved_handle(tmp_path: Path) -> None:
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["ok"], usage=UsageSnapshot(input_tokens=7, output_tokens=2))]
    )
    harness, store = _harness(tmp_path, streamer)

    result = await harness.run_role("generator", "do the thing", session_id="beat-1")

    assert result.session_id == "beat-1"
    handle = result.session_handle
    assert handle is not None
    assert handle.session_id == "beat-1"
    assert handle.path.exists()
    assert handle.usage_delta.input_tokens == 7
    assert handle.usage_total.input_tokens == 7
    assert store.list_sessions() == ["beat-1"]


async def test_second_run_under_same_id_resumes_transcript(tmp_path: Path) -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["first answer"], usage=UsageSnapshot(input_tokens=5)),
            FakeTurn(text_chunks=["second answer"], usage=UsageSnapshot(input_tokens=3)),
        ]
    )
    harness, _ = _harness(tmp_path, streamer)

    await harness.run_role("generator", "first ask", session_id="beat-1")
    second = await harness.run_role("generator", "second ask", session_id="beat-1")

    resumed = streamer.calls[1]
    assert [m.text for m in resumed] == ["first ask", "first answer", "second ask"]
    handle = second.session_handle
    assert handle is not None
    # The delta covers only this run; the total carries the whole thread.
    assert handle.usage_delta.input_tokens == 3
    assert handle.usage_total.input_tokens == 8


async def test_corrupt_snapshot_starts_thread_over_under_same_id(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    harness, store = _harness(tmp_path, streamer)
    store.path_for("beat-1").parent.mkdir(parents=True, exist_ok=True)
    store.path_for("beat-1").write_text("{truncated", encoding="utf-8")

    result = await harness.run_role("generator", "do the thing", session_id="beat-1")

    assert result.session_id == "beat-1"
    # Fresh thread: the model saw only the new intent.
    assert [m.text for m in streamer.calls[0]] == ["do the thing"]
    # The unusable snapshot was replaced, not left to fail every later run.
    assert store.load("beat-1").session_id == "beat-1"


async def test_snapshot_from_another_working_dir_starts_thread_over(tmp_path: Path) -> None:
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["first"]), FakeTurn(text_chunks=["second"])]
    )
    harness, store = _harness(tmp_path, streamer)
    await harness.run_role("generator", "first ask", session_id="beat-1")

    moved = Harness(
        HarnessConfig(
            working_dir=tmp_path / "elsewhere",
            paths=DreamPaths.resolve(tmp_path, home=tmp_path / "home"),
            session_store=store,
            _engine_factory=harness.config._engine_factory,
        )
    )
    await moved.run_role("generator", "second ask", session_id="beat-1")

    # A transcript about other files is not continuity — the thread restarts.
    assert [m.text for m in streamer.calls[1]] == ["second ask"]


async def test_transcript_is_saved_when_the_role_session_errors(tmp_path: Path) -> None:
    class ErroringStreamer(FakeStreamer):
        async def stream_turn(
            self, messages: list[ConversationMessage]
        ) -> AsyncIterator[StreamEvent]:
            self.calls.append(list(messages))
            yield ErrorEvent(message="provider exploded")

    streamer = ErroringStreamer(turns=[])
    harness, store = _harness(tmp_path, streamer)

    with pytest.raises(RoleSessionError):
        await harness.run_role("generator", "do the thing", session_id="beat-1")

    # The next beat should be able to read what went wrong.
    snapshot = store.load("beat-1")
    texts = [
        block.text
        for record in snapshot.messages
        for block in record.content
        if isinstance(block, TextBlockRecord)
    ]
    assert texts == ["do the thing"]
