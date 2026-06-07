"""Spec 04 stage 4c — progressive skill disclosure mechanism.

At session start only skill *frontmatter* (``name``, ``description``,
``when_to_use``) is loaded; the *body* loads only when the agent calls
``use_skill(name)``, emitting ``context.skill.loaded``. The body stays
for the session unless evicted by compaction.

This is the *mechanism* — Spec 06 owns skill authoring & registry rules,
and the disclosure module consumes only a validated skill catalogue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.services.context_log import ContextSkillLoaded
from dream.services.skill_disclosure import (
    SkillFrontmatter,
    SkillRegistry,
    discover_skill_frontmatter,
    parse_skill_frontmatter,
    read_skill_frontmatter,
)

# --- parse_skill_frontmatter ------------------------------------------------


SAMPLE_SKILL = """\
---
name: example-skill
description: Helps with examples.
when_to_use: When examples are needed.
---

# Body

Step one. Step two.
"""


def test_parse_skill_frontmatter_extracts_three_required_keys() -> None:
    fm, body = parse_skill_frontmatter(SAMPLE_SKILL)
    assert fm.name == "example-skill"
    assert fm.description == "Helps with examples."
    assert fm.when_to_use == "When examples are needed."
    assert body.lstrip().startswith("# Body")


def test_parse_skill_frontmatter_strips_trailing_whitespace() -> None:
    text = "---\nname:   foo\ndescription:   bar  \nwhen_to_use: baz\n---\nbody"
    fm, _ = parse_skill_frontmatter(text)
    assert fm.name == "foo"
    assert fm.description == "bar"
    assert fm.when_to_use == "baz"


def test_parse_skill_frontmatter_missing_required_raises() -> None:
    text = "---\nname: foo\n---\nbody"
    with pytest.raises(ValueError):
        parse_skill_frontmatter(text)


def test_parse_skill_frontmatter_without_delimiters_raises() -> None:
    with pytest.raises(ValueError):
        parse_skill_frontmatter("no frontmatter here, just body")


# --- discover_skill_frontmatter ---------------------------------------------


def test_discover_skill_frontmatter_loads_only_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha desc\nwhen_to_use: alpha use\n---\nlong body content"
    )
    (skills_dir / "beta").mkdir()
    (skills_dir / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: beta desc\nwhen_to_use: beta use\n---\nbeta body"
    )

    fronts = discover_skill_frontmatter([skills_dir])
    assert {fm.name for fm in fronts} == {"alpha", "beta"}


def test_discover_skill_frontmatter_ignores_non_skill_files(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\nwhen_to_use: w\n---\nbody"
    )
    (skills_dir / "README.md").write_text("# not a skill")

    fronts = discover_skill_frontmatter([skills_dir])
    assert {fm.name for fm in fronts} == {"alpha"}


def test_discover_skill_frontmatter_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert discover_skill_frontmatter([tmp_path / "no_such_dir"]) == []


# --- progressive disclosure: startup parses frontmatter only ----------------


def test_read_skill_frontmatter_stops_at_closing_fence(tmp_path: Path) -> None:
    """Frontmatter parse must NOT slurp the body — startup cost can't scale
    with body size (Spec 04 progressive disclosure).
    """
    skill = tmp_path / "SKILL.md"
    big_body = "BODY-LINE\n" * 100_000
    skill.write_text(
        "---\nname: huge\ndescription: d\nwhen_to_use: w\n---\n" + big_body,
        encoding="utf-8",
    )
    fm = read_skill_frontmatter(skill)
    assert fm.name == "huge"
    assert fm.description == "d"
    assert fm.when_to_use == "w"


def test_startup_does_not_read_full_body(monkeypatch, tmp_path: Path) -> None:
    """Discovery/registry bootstrap must not call ``Path.read_text`` (the
    whole-file slurp). The fix reads line-by-line and stops at the fence;
    this fails on any implementation that reads the full body at startup.
    """
    skills_dir = tmp_path / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\nwhen_to_use: w\n---\nBODY " * 5,
        encoding="utf-8",
    )

    original_read_text = Path.read_text

    def _no_slurp(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == _SKILL_FILENAME_FOR_TEST:
            raise AssertionError("startup must not slurp the full SKILL.md body")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _no_slurp)

    # Both entry points must avoid the full-body slurp at startup.
    fronts = discover_skill_frontmatter([skills_dir])
    assert {fm.name for fm in fronts} == {"alpha"}
    registry = SkillRegistry.from_dirs([skills_dir])
    assert registry.loaded_skills() == set()

    # But use_skill (deferred body load) is allowed to read the body.
    monkeypatch.undo()
    assert "BODY" in registry.use_skill("alpha")


_SKILL_FILENAME_FOR_TEST = "SKILL.md"


# --- SkillRegistry: progressive disclosure + event emission -----------------


def _seed_registry(tmp_path: Path) -> SkillRegistry:
    skills_dir = tmp_path / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\nwhen_to_use: w\n---\nALPHA BODY ALPHA BODY"
    )
    (skills_dir / "beta").mkdir()
    (skills_dir / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: d\nwhen_to_use: w\n---\nBETA BODY BETA BODY"
    )
    return SkillRegistry.from_dirs([skills_dir])


def test_skill_frontmatter_only_at_session_start(tmp_path: Path) -> None:
    """Registry holds frontmatter; bodies are NOT preloaded."""
    registry = _seed_registry(tmp_path)
    fronts = registry.list_frontmatter()
    assert {fm.name for fm in fronts} == {"alpha", "beta"}
    assert registry.loaded_skills() == set()


def test_skill_body_on_use_skill(tmp_path: Path) -> None:
    registry = _seed_registry(tmp_path)
    body = registry.use_skill("alpha")
    assert "ALPHA BODY" in body
    assert registry.loaded_skills() == {"alpha"}


def test_skill_body_stays_for_session(tmp_path: Path) -> None:
    registry = _seed_registry(tmp_path)
    body1 = registry.use_skill("alpha")
    body2 = registry.use_skill("alpha")
    assert body1 == body2
    # Still recorded as loaded.
    assert "alpha" in registry.loaded_skills()


def test_use_unknown_skill_raises(tmp_path: Path) -> None:
    registry = _seed_registry(tmp_path)
    with pytest.raises(KeyError):
        registry.use_skill("gamma")


def test_skill_load_emits_event(tmp_path: Path) -> None:
    registry = _seed_registry(tmp_path)
    events: list = []
    registry.use_skill("alpha", event_sink=events.append)
    assert len(events) == 1
    assert isinstance(events[0], ContextSkillLoaded)
    assert events[0].skill_name == "alpha"


def test_repeated_use_skill_only_emits_load_event_once(tmp_path: Path) -> None:
    """The event marks a *load*, not a re-use; second call must not re-emit."""
    registry = _seed_registry(tmp_path)
    events: list = []
    registry.use_skill("alpha", event_sink=events.append)
    registry.use_skill("alpha", event_sink=events.append)
    assert len(events) == 1


# --- SkillFrontmatter is a frozen, hashable record --------------------------


def test_skill_frontmatter_is_frozen() -> None:
    fm = SkillFrontmatter(name="a", description="d", when_to_use="w")
    with pytest.raises(AttributeError):
        fm.name = "b"  # type: ignore[misc]
