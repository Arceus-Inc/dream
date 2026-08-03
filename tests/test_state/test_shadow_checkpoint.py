"""Shadow filesystem checkpoints — Hermes-style pre-mutate snaps (SOTA #8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.contracts.hook import HookEvent, HookResult
from dream.state.shadow import (
    CheckpointOutcome,
    CheckpointReason,
    MutatingToolName,
    RestoreOutcome,
    ShadowCheckpointConfig,
    ShadowCheckpointHook,
    ShadowCheckpointManager,
    ShadowCheckpointStore,
)


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    (d / "README.md").write_text("hello\n", encoding="utf-8")
    return d


@pytest.fixture()
def store_root(tmp_path: Path) -> Path:
    return tmp_path / "checkpoints"


@pytest.fixture()
def mgr(store_root: Path) -> ShadowCheckpointManager:
    return ShadowCheckpointManager(
        store=ShadowCheckpointStore(base_dir=store_root),
        config=ShadowCheckpointConfig(enabled=True, max_snapshots=10),
    )


def test_disabled_manager_skips(work_dir: Path, store_root: Path) -> None:
    mgr = ShadowCheckpointManager(
        store=ShadowCheckpointStore(base_dir=store_root),
        config=ShadowCheckpointConfig(enabled=False),
    )
    result = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE)
    assert result.outcome is CheckpointOutcome.DISABLED


def test_ensure_takes_first_checkpoint(mgr: ShadowCheckpointManager, work_dir: Path) -> None:
    result = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE)
    assert result.outcome is CheckpointOutcome.TAKEN
    assert result.snapshot is not None
    assert result.snapshot.commit_sha
    listed = mgr.list_for(work_dir)
    assert len(listed) == 1
    assert listed[0].reason is CheckpointReason.BEFORE_WRITE_FILE


def test_dedup_same_turn(mgr: ShadowCheckpointManager, work_dir: Path) -> None:
    first = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE)
    second = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_BASH)
    assert first.outcome is CheckpointOutcome.TAKEN
    assert second.outcome is CheckpointOutcome.ALREADY_THIS_TURN


def test_new_turn_allows_another_when_changed(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    assert mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE).outcome is CheckpointOutcome.TAKEN
    mgr.begin_turn()
    (work_dir / "README.md").write_text("changed\n", encoding="utf-8")
    again = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_EDIT_FILE)
    assert again.outcome is CheckpointOutcome.TAKEN
    assert len(mgr.list_for(work_dir)) == 2


def test_no_changes_skips(mgr: ShadowCheckpointManager, work_dir: Path) -> None:
    assert mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE).outcome is CheckpointOutcome.TAKEN
    mgr.begin_turn()
    skipped = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_BASH)
    assert skipped.outcome is CheckpointOutcome.NO_CHANGES


def test_skips_home_and_root(mgr: ShadowCheckpointManager) -> None:
    assert mgr.ensure(Path("/"), reason=CheckpointReason.BEFORE_BASH).outcome is CheckpointOutcome.DIRECTORY_TOO_BROAD
    assert mgr.ensure(Path.home(), reason=CheckpointReason.BEFORE_BASH).outcome is CheckpointOutcome.DIRECTORY_TOO_BROAD


def test_restore_rolls_back_file(mgr: ShadowCheckpointManager, work_dir: Path) -> None:
    taken = mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_WRITE_FILE)
    assert taken.snapshot is not None
    sha = taken.snapshot.commit_sha
    (work_dir / "README.md").write_text("mutated\n", encoding="utf-8")
    mgr.begin_turn()
    restored = mgr.restore(work_dir, commit_sha=sha)
    assert restored.outcome is RestoreOutcome.RESTORED
    assert (work_dir / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_mutating_tool_enum_covers_craft_tools() -> None:
    names = {t.value for t in MutatingToolName}
    assert {"write_file", "edit_file", "bash", "execute_code"}.issubset(names)


@pytest.mark.asyncio
async def test_hook_checkpoints_before_mutating_tool(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    hook = ShadowCheckpointHook(manager=mgr, working_dir=work_dir)
    result = await hook(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "write_file", "tool_input": {"path": "README.md"}},
    )
    assert isinstance(result, HookResult)
    assert result.blocked is False
    assert mgr.list_for(work_dir)


@pytest.mark.asyncio
async def test_hook_ignores_non_mutating_tool(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    hook = ShadowCheckpointHook(manager=mgr, working_dir=work_dir)
    await hook(HookEvent.PRE_TOOL_USE, {"tool_name": "read_file", "tool_input": {}})
    assert mgr.list_for(work_dir) == []


@pytest.mark.asyncio
async def test_hook_begin_turn_on_user_prompt(
    mgr: ShadowCheckpointManager, work_dir: Path
) -> None:
    hook = ShadowCheckpointHook(manager=mgr, working_dir=work_dir)
    await hook(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "write_file", "tool_input": {}},
    )
    assert mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_BASH).outcome is CheckpointOutcome.ALREADY_THIS_TURN
    await hook(HookEvent.USER_PROMPT_SUBMIT, {"session_id": "s1", "prompt": "go"})
    (work_dir / "README.md").write_text("next\n", encoding="utf-8")
    assert mgr.ensure(work_dir, reason=CheckpointReason.BEFORE_BASH).outcome is CheckpointOutcome.TAKEN
