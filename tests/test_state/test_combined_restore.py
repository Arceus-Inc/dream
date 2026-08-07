"""Eval: combined FS + transcript restore (Hermes /rollback)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.engine._messages import ConversationMessage, TextBlock
from dream.state.shadow import (
    CheckpointReason,
    CombinedRestoreResult,
    RestoreOutcome,
    ShadowCheckpointConfig,
    ShadowCheckpointManager,
    ShadowCheckpointStore,
)


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    (d / "README.md").write_text("v1\n", encoding="utf-8")
    return d


@pytest.fixture()
def mgr(tmp_path: Path) -> ShadowCheckpointManager:
    return ShadowCheckpointManager(
        store=ShadowCheckpointStore(base_dir=tmp_path / "checkpoints"),
        config=ShadowCheckpointConfig(enabled=True, max_snapshots=10),
    )


def _msgs(*texts: str) -> list[ConversationMessage]:
    out: list[ConversationMessage] = []
    for i, text in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        out.append(ConversationMessage(role=role, content=[TextBlock(text=text)]))
    return out


def test_restore_and_rewind_aligns_fs_and_transcript(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    taken = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE)
    assert taken.outcome.value == "taken"
    assert taken.snapshot is not None
    sha = taken.snapshot.commit_sha

    (work_dir / "README.md").write_text("v2-broken\n", encoding="utf-8")
    messages = _msgs("fix the bug", "I edited README", "looks good?", "ship it")

    result = mgr.restore_and_rewind(
        work_dir,
        commit_sha=sha,
        messages=messages,
        rewind_turns=1,
    )
    assert isinstance(result, CombinedRestoreResult)
    assert result.fs.outcome is RestoreOutcome.RESTORED
    assert (work_dir / "README.md").read_text(encoding="utf-8") == "v1\n"
    assert result.messages == tuple(messages[:2])
    assert result.transcript_removed == 2


def test_restore_and_rewind_fs_only_when_rewind_zero(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    taken = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_EDIT_FILE)
    assert taken.snapshot is not None
    (work_dir / "README.md").write_text("changed\n", encoding="utf-8")
    messages = _msgs("a", "b")

    result = mgr.restore_and_rewind(
        work_dir,
        commit_sha=taken.snapshot.commit_sha,
        messages=messages,
        rewind_turns=0,
    )
    assert result.fs.outcome is RestoreOutcome.RESTORED
    assert result.messages == tuple(messages)
    assert result.transcript_removed == 0


def test_restore_and_rewind_propagates_fs_failure(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    messages = _msgs("a", "b")
    result = mgr.restore_and_rewind(
        work_dir,
        commit_sha="deadbeef" * 5,
        messages=messages,
        rewind_turns=1,
    )
    assert result.fs.outcome is RestoreOutcome.NOT_FOUND
    assert result.messages == tuple(messages)
    assert result.transcript_removed == 0
