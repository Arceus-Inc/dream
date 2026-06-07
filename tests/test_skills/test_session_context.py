"""Spec 06 Slice 2 — SkillContext metadata channel + session registry builder."""

from __future__ import annotations

from pathlib import Path

from dream.skills._registry import SkillRegistry
from dream.skills._session import (
    SKILL_CONTEXT_KEY,
    SkillContext,
    build_session_skill_registry,
    put_skill_context,
    read_skill_context,
)
from tests.test_skills._helpers import write_skill


def test_put_and_read_round_trip() -> None:
    sc = SkillContext(registry=SkillRegistry(), available_tools=frozenset({"bash"}))
    metadata: dict[str, object] = {}
    put_skill_context(metadata, sc)
    assert metadata[SKILL_CONTEXT_KEY] is sc
    assert read_skill_context(metadata) is sc


def test_read_absent_returns_none() -> None:
    assert read_skill_context({}) is None


def test_read_wrong_type_returns_none() -> None:
    assert read_skill_context({SKILL_CONTEXT_KEY: "not a context"}) is None


def test_build_session_registry_layers_user_and_project(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    write_skill(home / "skills", "globalskill")
    write_skill(repo / "docs" / "skills", "projskill")

    registry, shadows = build_session_skill_registry(repo, home=home)

    glob = registry.resolve("globalskill")
    proj = registry.resolve("projskill")
    assert glob is not None and glob.source == "user"
    assert proj is not None and proj.source == "project"
    assert shadows == []


def test_build_session_registry_project_gate(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    write_skill(repo / "docs" / "skills", "projskill")
    registry, _ = build_session_skill_registry(repo, home=home, allow_project_skills=False)
    assert registry.resolve("projskill") is None
