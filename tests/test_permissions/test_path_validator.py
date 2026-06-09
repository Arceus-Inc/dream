"""Spec 13A — repo-write boundary validator.

A write is in-bounds iff its resolved path is under the worktree cwd or under an
``extra_allowed`` root. ``resolve()`` collapses symlinks, so symlink-escape is
denied. Non-existent targets still boundary-check (strict=False).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.permissions._path_validator import validate_repo_write


def test_write_inside_cwd_is_allowed(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    ok, reason = validate_repo_write(cwd / "src" / "main.py", cwd)
    assert ok
    assert reason == ""


def test_write_outside_cwd_is_denied_with_reason(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    ok, reason = validate_repo_write(outside / "x.txt", cwd)
    assert not ok
    assert "x.txt" in reason
    assert "repo" in reason


def test_relative_path_is_anchored_at_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    ok, _ = validate_repo_write(Path("notes.md"), cwd)
    assert ok


def test_extra_allowed_root_is_permitted(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    ok, _ = validate_repo_write(extra / "f.txt", cwd, (extra,))
    assert ok


def test_outside_all_roots_is_denied(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    ok, _ = validate_repo_write(other / "f.txt", cwd, (extra,))
    assert not ok


def test_symlink_escape_is_denied(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (cwd / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # restricted runners can't create symlinks
        pytest.skip(f"symlink creation unsupported here: {exc}")
    ok, _ = validate_repo_write(cwd / "escape" / "f.txt", cwd)
    assert not ok


def test_nonexistent_target_inside_cwd_is_allowed(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    ok, _ = validate_repo_write(cwd / "new" / "deep" / "file.txt", cwd)
    assert ok


def test_cwd_root_itself_is_allowed(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    ok, _ = validate_repo_write(cwd, cwd)
    assert ok
