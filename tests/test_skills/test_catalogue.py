"""Spec 06 Slice 2 — skill frontmatter catalogue for the model prompt."""

from __future__ import annotations

from dream.skills._catalogue import render_skill_catalogue
from dream.skills._types import SkillMeta


def _meta(name: str, **kw: object) -> SkillMeta:
    return SkillMeta(
        name=name,
        description=f"{name} description",
        when_to_use=f"use {name} when relevant",
        source="bundled",
        **kw,  # type: ignore[arg-type]
    )


def test_render_includes_model_invocable_skill() -> None:
    out = render_skill_catalogue([_meta("refactor")])
    assert "refactor" in out
    assert "refactor description" in out
    assert "use refactor when relevant" in out


def test_render_excludes_user_only_skills() -> None:
    out = render_skill_catalogue(
        [_meta("release", disable_model_invocation=True), _meta("refactor")]
    )
    assert "refactor" in out
    assert "release" not in out


def test_render_empty_when_no_model_skills() -> None:
    assert render_skill_catalogue([]) == ""
    assert render_skill_catalogue([_meta("release", disable_model_invocation=True)]) == ""
