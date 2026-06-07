"""Spec 06 — layered skill loader: precedence, project gate, shadow records."""

from __future__ import annotations

from pathlib import Path

from dream.skills._loader import discover_skill_metas, load_skill_registry
from tests.test_skills._helpers import write_skill


def test_discover_skill_metas_reads_frontmatter(tmp_path: Path) -> None:
    write_skill(tmp_path, "alpha", description="A.")
    write_skill(tmp_path, "beta", description="B.")
    metas = discover_skill_metas([tmp_path], source="user")
    by_name = {m.name: m for m in metas}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"].source == "user"


def test_frontmatter_only_at_session_start(tmp_path: Path) -> None:
    """MUST #1: building the registry surfaces frontmatter, never bodies."""
    write_skill(tmp_path, "refactor", body="A 500-LINE BODY")
    registry, shadows = load_skill_registry(user_dirs=[tmp_path])
    assert [m.name for m in registry.list_meta()] == ["refactor"]
    assert registry.loaded_skills() == set()
    assert shadows == []


def test_layered_precedence_project_shadows_bundled(tmp_path: Path) -> None:
    bundled_dir = tmp_path / "bundled"
    project_dir = tmp_path / "project"
    write_skill(bundled_dir, "deploy", description="bundled deploy")
    write_skill(project_dir, "deploy", description="project deploy")

    bundled = discover_skill_metas([bundled_dir], source="bundled")
    registry, shadows = load_skill_registry(
        bundled=bundled, project_dirs=[project_dir]
    )

    winner = registry.resolve("deploy")
    assert winner is not None
    assert winner.source == "project"
    assert winner.description == "project deploy"
    assert len(shadows) == 1
    assert shadows[0].name == "deploy"
    assert shadows[0].winner_source == "project"
    assert shadows[0].shadowed_source == "bundled"


def test_project_skills_gated_by_setting(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    write_skill(project_dir, "local_helper")
    registry, _ = load_skill_registry(
        project_dirs=[project_dir], allow_project_skills=False
    )
    assert registry.resolve("local_helper") is None


def test_plugin_skills_registered_last(tmp_path: Path) -> None:
    user_dir = tmp_path / "user"
    write_skill(user_dir, "deploy", description="user deploy")
    plugin = discover_skill_metas(
        [_with_skill(tmp_path / "plugin", "deploy", "plugin deploy")], source="plugin"
    )
    registry, shadows = load_skill_registry(user_dirs=[user_dir], plugin_skills=plugin)
    assert registry.resolve("deploy").source == "plugin"  # type: ignore[union-attr]
    assert any(s.winner_source == "plugin" for s in shadows)


def _with_skill(root: Path, slug: str, description: str) -> Path:
    write_skill(root, slug, description=description)
    return root
