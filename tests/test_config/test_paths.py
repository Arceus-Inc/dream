"""Spec 01 — the two storage roots and the paths derived from them.

`DreamPaths` is a pure value object: resolving it or reading a path property must
never touch the filesystem. Directory creation is the single explicit `ensure()`.
Naming is locked to `.dream` (repo) and `~/.dream` (home).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dream.config.paths import CHECKPOINT_REF_PREFIX, DREAM_HOME_ENV, DreamPaths


def test_resolve_makes_repo_absolute() -> None:
    paths = DreamPaths.resolve(".", env={})
    assert paths.repo.is_absolute()


def test_resolve_defaults_home_to_dot_dream_under_user_home(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={})
    assert paths.home == Path.home() / ".dream"


def test_resolve_honours_dream_home_env(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={DREAM_HOME_ENV: str(tmp_path / "h")})
    assert paths.home == (tmp_path / "h").resolve()


def test_explicit_home_overrides_env(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(
        tmp_path, home=tmp_path / "explicit", env={DREAM_HOME_ENV: str(tmp_path / "env")}
    )
    assert paths.home == (tmp_path / "explicit").resolve()


def test_empty_dream_home_env_raises(tmp_path: Path) -> None:
    # Present-but-blank is a misconfiguration, not a silent fallback to ~/.dream.
    with pytest.raises(ValueError):
        DreamPaths.resolve(tmp_path, env={DREAM_HOME_ENV: ""})


def test_repo_side_now_state_layout(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.dream_dir == p.repo / ".dream"
    assert p.worktrees_dir == p.repo / ".dream" / "worktrees"
    assert p.sidecars_dir == p.repo / ".dream" / "sidecars"
    assert p.coordination_dir == p.repo / ".dream" / "coordination"
    assert p.coordination_board == p.repo / ".dream" / "coordination" / "board.sqlite"


def test_of_record_layout(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.docs_dir == p.repo / "docs"
    assert p.exec_plans_active == p.repo / "docs" / "exec-plans" / "active"
    assert p.schemas_dir == p.repo / "docs" / "_schemas"
    assert p.agents_md == p.repo / "AGENTS.md"


def test_per_task_paths(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.worktree("T1") == p.worktrees_dir / "T1"
    assert p.sidecar("T1") == p.sidecars_dir / "T1"


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "a\\b", "/abs"])
def test_task_id_methods_reject_traversal(tmp_path: Path, bad: str) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    with pytest.raises(ValueError):
        p.worktree(bad)
    with pytest.raises(ValueError):
        p.sidecar(bad)
    with pytest.raises(ValueError):
        p.checkpoint_ref(bad, 1)


def test_role_manifest_overlay_path(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.role_manifest_overlay("planner") == p.repo / ".harness" / "roles" / "planner.toml"


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "a\\b", "/abs"])
def test_role_manifest_overlay_rejects_traversal(tmp_path: Path, bad: str) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    with pytest.raises(ValueError):
        p.role_manifest_overlay(bad)


def test_checkpoint_ref_format(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.checkpoint_ref("T1", 3) == f"{CHECKPOINT_REF_PREFIX}/T1/3"
    assert p.checkpoint_ref("T1", "done") == f"{CHECKPOINT_REF_PREFIX}/T1/done"


def test_home_side_paths(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, home=tmp_path / "h", env={})
    assert p.settings_file == p.home / "settings.json"
    assert p.sessions_dir == p.home / "data" / "sessions"
    assert p.tasks_dir == p.home / "data" / "tasks"
    assert p.memory_dir == p.home / "memory"
    assert p.skills_dir == p.home / "skills"


def test_reading_properties_creates_nothing(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, home=tmp_path / "h", env={})
    _ = (
        p.dream_dir,
        p.worktrees_dir,
        p.sidecars_dir,
        p.coordination_dir,
        p.coordination_board,
        p.docs_dir,
        p.exec_plans_active,
        p.schemas_dir,
        p.agents_md,
        p.settings_file,
        p.sessions_dir,
        p.tasks_dir,
        p.memory_dir,
        p.skills_dir,
        p.worktree("T1"),
        p.sidecar("T1"),
    )
    assert not p.dream_dir.exists()
    assert not p.home.exists()


def test_ensure_creates_now_state_dirs_only(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, home=tmp_path / "h", env={})
    returned = p.ensure()
    assert returned is p
    assert p.worktrees_dir.is_dir()
    assert p.sidecars_dir.is_dir()
    assert p.coordination_dir.is_dir()
    assert not p.docs_dir.exists()  # of-record is committed, never fabricated


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    p.ensure()
    p.ensure()
    assert p.worktrees_dir.is_dir()


def test_dreampaths_is_frozen(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    with pytest.raises(FrozenInstanceError):
        p.repo = tmp_path  # type: ignore[misc]


def test_sandbox_config_path(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.sandbox_config() == p.repo / ".harness" / "sandbox.toml"


def test_tool_tier_overrides_path(tmp_path: Path) -> None:
    p = DreamPaths.resolve(tmp_path, env={})
    assert p.tool_tier_overrides() == p.repo / ".harness" / "tool-tier-overrides.toml"
