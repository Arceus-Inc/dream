"""Spec 01 — WorktreeSlug / WorktreeInfo value objects (the validated contract).

`WorktreeSlug` validates on construction so an unvalidated slug can never reach a
filesystem or git operation; downstream code takes the type, not a raw string.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dream.swarm._worktree import WorktreeInfo, WorktreeSlug


def test_slug_validates_on_construction() -> None:
    with pytest.raises(ValueError):
        WorktreeSlug("../escape")


def test_slug_value_preserved() -> None:
    assert WorktreeSlug("feature/login").value == "feature/login"


def test_slug_flat_replaces_slash() -> None:
    assert WorktreeSlug("feature/login/v2").flat == "feature+login+v2"


def test_slug_branch_name() -> None:
    assert WorktreeSlug("feature/login").branch == "worktree-feature+login"


def test_slug_is_frozen() -> None:
    slug = WorktreeSlug("task-1")
    with pytest.raises(FrozenInstanceError):
        slug.value = "other"  # type: ignore[misc]


def test_slug_equality_by_value() -> None:
    assert WorktreeSlug("task-1") == WorktreeSlug("task-1")


def test_worktree_info_fields() -> None:
    info = WorktreeInfo(
        slug="task-1",
        path=Path("/wt/task-1"),
        branch="worktree-task-1",
        original_path=Path("/repo"),
        created_at=123.0,
        agent_id="A1",
    )
    assert info.slug == "task-1"
    assert info.agent_id == "A1"


def test_worktree_info_agent_id_optional() -> None:
    info = WorktreeInfo(
        slug="t",
        path=Path("/wt/t"),
        branch="worktree-t",
        original_path=Path("/repo"),
        created_at=1.0,
    )
    assert info.agent_id is None
