"""Spec 06 — SkillRegistry: lookup, lazy bodies, shadow records, loaded event."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.services.context_log import ContextEvent, ContextSkillLoaded
from dream.skills._frontmatter import read_skill_meta
from dream.skills._registry import SkillRegistry
from dream.skills._types import SkillMeta
from tests.test_skills._helpers import write_skill


def _meta(name: str, *, source: str = "bundled", **kw: object) -> SkillMeta:
    return SkillMeta(
        name=name,
        description=f"{name} desc",
        when_to_use=f"use {name}",
        source=source,  # type: ignore[arg-type]
        command_name=kw.get("command_name", name),  # type: ignore[arg-type]
        aliases=kw.get("aliases", ()),  # type: ignore[arg-type]
        path=kw.get("path"),  # type: ignore[arg-type]
    )


def test_register_and_resolve_by_name() -> None:
    reg = SkillRegistry()
    assert reg.register(_meta("refactor")) is None
    assert reg.resolve("refactor") is not None
    assert reg.resolve("refactor").name == "refactor"  # type: ignore[union-attr]


def test_resolve_tolerates_case_variants() -> None:
    reg = SkillRegistry()
    reg.register(_meta("refactor"))
    assert reg.resolve("Refactor") is not None
    assert reg.resolve("REFACTOR") is not None


def test_resolve_by_alias_and_command_name() -> None:
    reg = SkillRegistry()
    reg.register(_meta("deploy", command_name="ship", aliases=("release",)))
    assert reg.resolve("ship") is not None
    assert reg.resolve("release") is not None


def test_register_returns_shadow_on_cross_source_collision() -> None:
    reg = SkillRegistry()
    assert reg.register(_meta("deploy", source="bundled")) is None
    shadow = reg.register(_meta("deploy", source="project"))
    assert shadow is not None
    assert shadow.name == "deploy"
    assert shadow.winner_source == "project"
    assert shadow.shadowed_source == "bundled"
    # winner is now resolvable
    assert reg.resolve("deploy").source == "project"  # type: ignore[union-attr]


def test_list_meta_is_name_sorted() -> None:
    reg = SkillRegistry()
    for n in ("zebra", "alpha", "mango"):
        reg.register(_meta(n))
    assert [m.name for m in reg.list_meta()] == ["alpha", "mango", "zebra"]


def test_use_skill_loads_body_and_emits_event(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "refactor", body="THE BODY")
    reg = SkillRegistry()
    reg.register(read_skill_meta(path, source="project"))

    events: list[ContextEvent] = []
    defn = reg.use_skill("refactor", event_sink=events.append)
    assert "THE BODY" in defn.content
    assert defn.meta.name == "refactor"
    assert len(events) == 1
    assert isinstance(events[0], ContextSkillLoaded)
    assert events[0].skill_name == "refactor"


def test_use_skill_caches_body_and_emits_once(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "refactor", body="ONCE")
    reg = SkillRegistry()
    reg.register(read_skill_meta(path, source="project"))

    events: list[ContextEvent] = []
    first = reg.use_skill("refactor", event_sink=events.append)
    second = reg.use_skill("refactor", event_sink=events.append)
    assert first.content == second.content
    assert len(events) == 1  # cached: no second load event
    assert reg.loaded_skills() == {"refactor"}


def test_list_meta_does_not_load_bodies(tmp_path: Path) -> None:
    """Progressive disclosure: enumerating frontmatter must not read bodies."""
    path = write_skill(tmp_path, "refactor", body="SHOULD NOT LOAD")
    reg = SkillRegistry()
    reg.register(read_skill_meta(path, source="project"))
    reg.list_meta()
    assert reg.loaded_skills() == set()


def test_use_skill_unknown_raises() -> None:
    reg = SkillRegistry()
    with pytest.raises(KeyError):
        reg.use_skill("nope")
