"""Spec 10 slices B+C — worktree-scoped directory layout for swarm IPC.

The deliberate divergence from OpenHarness: every byte of swarm state lives
under the *worktree's* ``.harness/swarm/{leader}/...`` tree, never under
``~/.openharness`` or any home/user-scope directory. The reason is the
spec-00 "repo is the system of record" rule: a swarm message that survives
on disk must be committable (or at minimum inspectable + diffable in the
worktree). A home-dir mailbox breaks that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.swarm._paths import (
    leader_inbox_dir,
    leader_permissions_dir,
    leader_swarm_dir,
    validate_leader_id,
)


def test_leader_swarm_dir_is_under_worktree_dot_harness(tmp_path: Path) -> None:
    out = leader_swarm_dir(tmp_path, "planner")
    assert out == tmp_path / ".harness" / "swarm" / "planner"


def test_leader_inbox_dir_is_under_swarm(tmp_path: Path) -> None:
    out = leader_inbox_dir(tmp_path, "planner")
    assert out == tmp_path / ".harness" / "swarm" / "planner" / "inbox"


def test_leader_permissions_dir_is_under_swarm(tmp_path: Path) -> None:
    out = leader_permissions_dir(tmp_path, "planner")
    assert out == tmp_path / ".harness" / "swarm" / "planner" / "permissions"


def test_helpers_do_not_create_directories_implicitly(tmp_path: Path) -> None:
    # Path computation is pure; mailbox writes own directory creation so a
    # call that *only* asks for the path doesn't leave debris.
    _ = leader_inbox_dir(tmp_path, "planner")
    assert not (tmp_path / ".harness").exists()


# --- leader id validation (security boundary; same shape as worktree slug) ---


@pytest.mark.parametrize("bad", ["", "../escape", "with/slash", "a:b", "a b"])
def test_validate_leader_id_rejects_unsafe_values(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_leader_id(bad)


@pytest.mark.parametrize("good", ["planner", "generator", "planner-1", "eval_1", "a.b"])
def test_validate_leader_id_accepts_safe_values(good: str) -> None:
    assert validate_leader_id(good) == good


def test_leader_inbox_dir_validates_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        leader_inbox_dir(tmp_path, "../etc/passwd")
