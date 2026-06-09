"""Spec 10 slice A — layered manifest loader.

Bundled defaults are the floor; the project overlay (``.harness/roles/{role}.toml``)
wins on a per-field basis. Unknown role names raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.roles import load_role_manifest

# --- defaults flow through when no overlay exists ---------------------------


def test_loader_returns_bundled_default_when_no_overlay(tmp_path: Path) -> None:
    m = load_role_manifest("planner", harness_dir=tmp_path / ".harness")
    assert m.name == "planner"
    assert m.tools is not None
    assert "read_file" in m.tools


# --- overlay wins per-field --------------------------------------------------


def test_overlay_replaces_named_fields(tmp_path: Path) -> None:
    harness_dir = tmp_path / ".harness"
    roles_dir = harness_dir / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "planner.toml").write_text(
        'description = "operator-tuned planner"\n'
        'effort = "high"\n',
        encoding="utf-8",
    )

    m = load_role_manifest("planner", harness_dir=harness_dir)

    assert m.description == "operator-tuned planner"
    assert m.effort == "high"
    # Untouched fields keep the bundled defaults.
    assert m.name == "planner"
    assert m.permission_mode == "plan"
    assert m.tools is not None and "read_file" in m.tools


def test_overlay_may_extend_disallowed_tools(tmp_path: Path) -> None:
    harness_dir = tmp_path / ".harness"
    roles_dir = harness_dir / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "planner.toml").write_text(
        'disallowed_tools = ["file_write", "file_edit", "bash", "git"]\n',
        encoding="utf-8",
    )

    m = load_role_manifest("planner", harness_dir=harness_dir)
    assert "git" in m.disallowed_tools


def test_overlay_with_invalid_field_value_raises(tmp_path: Path) -> None:
    harness_dir = tmp_path / ".harness"
    roles_dir = harness_dir / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "planner.toml").write_text(
        'permission_mode = "bypassPermissions"\n',
        encoding="utf-8",
    )

    with pytest.raises(Exception):  # ValidationError, exact type private to pydantic
        load_role_manifest("planner", harness_dir=harness_dir)


def test_loader_rejects_unknown_role_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_role_manifest("ceo", harness_dir=tmp_path / ".harness")  # type: ignore[arg-type]


def test_loader_does_not_create_overlay_files(tmp_path: Path) -> None:
    harness_dir = tmp_path / ".harness"
    load_role_manifest("planner", harness_dir=harness_dir)
    # The loader is read-only; no side effects on the filesystem.
    assert not (harness_dir / "roles" / "planner.toml").exists()
