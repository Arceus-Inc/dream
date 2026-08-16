"""Eval: Session.list_checkpoints / restore_checkpoint (Hermes human rewind)."""

from __future__ import annotations

from pathlib import Path
from typing import Never

import pytest

from dream.engine._engine import QueryEngine
from dream.engine._messages import ConversationMessage, TextBlock
from dream.session import Session
from dream.state.shadow import (
    CheckpointReason,
    RestoreOutcome,
    ShadowCheckpointConfig,
    ShadowCheckpointManager,
    ShadowCheckpointStore,
)


class _NoopStreamer:
    async def stream_turn(self, *_args: object, **_kwargs: object) -> Never:
        raise RuntimeError("streamer unused")
        yield  # pragma: no cover — makes this an async generator type


class _NoopDispatcher:
    async def dispatch(self, *_args: object, **_kwargs: object) -> tuple[str, bool]:
        return ("", False)


def _engine(working_dir: Path, manager: ShadowCheckpointManager | None) -> QueryEngine:
    return QueryEngine(
        streamer=_NoopStreamer(),  # type: ignore[arg-type]
        dispatcher=_NoopDispatcher(),  # type: ignore[arg-type]
        session_id="s1",
        working_dir=working_dir,
        checkpoint_manager=manager,
    )


def test_session_restore_checkpoint_rewinds_fs_and_transcript(tmp_path: Path) -> None:
    work = tmp_path / "proj"
    work.mkdir()
    (work / "f.txt").write_text("clean\n", encoding="utf-8")
    mgr = ShadowCheckpointManager(
        store=ShadowCheckpointStore(base_dir=tmp_path / "ck"),
        config=ShadowCheckpointConfig(enabled=True),
    )
    snap = mgr.ensure(work, reason=CheckpointReason.BEFORE_WRITE_FILE)
    assert snap.snapshot is not None

    (work / "f.txt").write_text("dirty\n", encoding="utf-8")
    session = Session(id="s1", _engine=_engine(work, mgr))
    session.transcript.extend(
        [
            ConversationMessage(role="user", content=[TextBlock(text="edit it")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
            ConversationMessage(role="user", content=[TextBlock(text="more")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="more done")]),
        ]
    )
    session._prompt_indices.extend([0, 2])

    result = session.restore_checkpoint(rewind_turns=1)
    assert result.fs.outcome is RestoreOutcome.RESTORED
    assert (work / "f.txt").read_text(encoding="utf-8") == "clean\n"
    assert len(session.transcript) == 2
    assert session.transcript[0].text == "edit it"
    assert result.transcript_removed == 2


def test_session_list_checkpoints_empty_without_manager(tmp_path: Path) -> None:
    session = Session(id="s1", _engine=_engine(tmp_path, None))
    assert session.list_checkpoints() == []
    result = session.restore_checkpoint()
    assert result.fs.outcome is RestoreOutcome.DISABLED


def test_session_restore_refuses_during_active_send(tmp_path: Path) -> None:
    work = tmp_path / "proj"
    work.mkdir()
    mgr = ShadowCheckpointManager(
        store=ShadowCheckpointStore(base_dir=tmp_path / "ck"),
        config=ShadowCheckpointConfig(enabled=True),
    )
    session = Session(id="s1", _engine=_engine(work, mgr))
    session._active = True
    with pytest.raises(RuntimeError, match="in flight"):
        session.restore_checkpoint()


def test_session_restore_unavailable_after_compaction(tmp_path: Path) -> None:
    work = tmp_path / "proj"
    work.mkdir()
    (work / "f.txt").write_text("clean\n", encoding="utf-8")
    mgr = ShadowCheckpointManager(
        store=ShadowCheckpointStore(base_dir=tmp_path / "ck"),
        config=ShadowCheckpointConfig(enabled=True),
    )
    snap = mgr.ensure(work, reason=CheckpointReason.BEFORE_WRITE_FILE)
    assert snap.snapshot is not None
    (work / "f.txt").write_text("dirty\n", encoding="utf-8")

    session = Session(id="s1", _engine=_engine(work, mgr))
    session.transcript.extend(
        [
            ConversationMessage(role="user", content=[TextBlock(text="compacted")]),
        ]
    )
    session._prompt_indices.clear()

    result = session.restore_checkpoint(rewind_turns=1)
    assert result.fs.outcome is RestoreOutcome.FAILED
    assert "unavailable" in result.fs.detail
    assert (work / "f.txt").read_text(encoding="utf-8") == "dirty\n"
    assert len(session.transcript) == 1
