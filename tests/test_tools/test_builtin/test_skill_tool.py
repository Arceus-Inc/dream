"""Spec 06 Slice 2 — the ``skill`` tool: lookup, governance, dependency gating."""

from __future__ import annotations

from pathlib import Path

from dream.services.context_log import ContextEvent, ContextSkillLoaded
from dream.skills._frontmatter import read_skill_meta
from dream.skills._registry import SkillRegistry
from dream.skills._session import SkillContext, put_skill_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import default_registry
from dream.tools.builtin.skill import SkillTool
from tests.test_skills._helpers import write_skill


def _ctx(working_dir: Path, skill_context: SkillContext | None) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if skill_context is not None:
        put_skill_context(metadata, skill_context)
    return ToolExecutionContext(
        working_dir=working_dir, session_id="s_test", metadata=metadata
    )


def _registry_with(path: Path) -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(read_skill_meta(path, source="project"))
    return reg


def test_skill_tool_is_read_only() -> None:
    assert SkillTool().is_read_only() is True
    assert SkillTool().declaration.tier_required == 0


def test_default_registry_registers_skill() -> None:
    assert default_registry().get("skill") is not None


async def test_loads_body_and_emits_event(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "refactor", body="THE PLAYBOOK BODY")
    reg = _registry_with(path)
    events: list[ContextEvent] = []
    sc = SkillContext(registry=reg, available_tools=frozenset(), event_sink=events.append)

    result = await SkillTool().execute({"name": "refactor"}, _ctx(tmp_path, sc))

    assert result.is_error is False
    assert "THE PLAYBOOK BODY" in result.content
    assert any(isinstance(e, ContextSkillLoaded) for e in events)


async def test_case_variant_lookup(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "refactor", body="B")
    sc = SkillContext(registry=_registry_with(path), available_tools=frozenset())
    result = await SkillTool().execute({"name": "Refactor"}, _ctx(tmp_path, sc))
    assert result.is_error is False


async def test_not_found_is_structured_error(tmp_path: Path) -> None:
    sc = SkillContext(registry=SkillRegistry(), available_tools=frozenset())
    result = await SkillTool().execute({"name": "ghost"}, _ctx(tmp_path, sc))
    assert result.is_error is True
    assert "not found" in result.content.lower()
    assert "root_cause" in result.metadata


async def test_missing_skill_context_is_error(tmp_path: Path) -> None:
    result = await SkillTool().execute({"name": "x"}, _ctx(tmp_path, None))
    assert result.is_error is True


async def test_disable_model_invocation_refused_for_model(tmp_path: Path) -> None:
    path = write_skill(
        tmp_path, "release", extra_frontmatter="disable_model_invocation: true"
    )
    reg = _registry_with(path)
    sc = SkillContext(registry=reg, available_tools=frozenset())

    result = await SkillTool().execute({"name": "release"}, _ctx(tmp_path, sc))

    assert result.is_error is True
    assert "/release" in result.content
    assert reg.loaded_skills() == set()  # no body loaded


async def test_tools_required_missing_refused(tmp_path: Path) -> None:
    path = write_skill(
        tmp_path, "browse", extra_frontmatter="tools_required: [browser_drive]"
    )
    reg = _registry_with(path)
    sc = SkillContext(registry=reg, available_tools=frozenset({"bash"}))

    result = await SkillTool().execute({"name": "browse"}, _ctx(tmp_path, sc))

    assert result.is_error is True
    assert "browser_drive" in result.content
    assert reg.loaded_skills() == set()


async def test_tools_required_present_loads(tmp_path: Path) -> None:
    path = write_skill(
        tmp_path, "vcs", extra_frontmatter="tools_required: [bash, git]"
    )
    reg = _registry_with(path)
    sc = SkillContext(registry=reg, available_tools=frozenset({"bash", "git"}))

    result = await SkillTool().execute({"name": "vcs"}, _ctx(tmp_path, sc))

    assert result.is_error is False
    assert reg.loaded_skills() == {"vcs"}
