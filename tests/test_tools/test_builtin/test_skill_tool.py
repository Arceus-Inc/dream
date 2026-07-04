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
    return ToolExecutionContext(working_dir=working_dir, session_id="s_test", metadata=metadata)


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
    path = write_skill(tmp_path, "release", extra_frontmatter="disable_model_invocation: true")
    reg = _registry_with(path)
    sc = SkillContext(registry=reg, available_tools=frozenset())

    result = await SkillTool().execute({"name": "release"}, _ctx(tmp_path, sc))

    assert result.is_error is True
    assert "/release" in result.content
    assert reg.loaded_skills() == set()  # no body loaded


async def test_tools_required_missing_refused(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "browse", extra_frontmatter="tools_required: [browser_drive]")
    reg = _registry_with(path)
    sc = SkillContext(registry=reg, available_tools=frozenset({"bash"}))

    result = await SkillTool().execute({"name": "browse"}, _ctx(tmp_path, sc))

    assert result.is_error is True
    assert "browser_drive" in result.content
    assert reg.loaded_skills() == set()


async def test_tools_required_present_loads(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "vcs", extra_frontmatter="tools_required: [bash, git]")
    reg = _registry_with(path)
    sc = SkillContext(registry=reg, available_tools=frozenset({"bash", "git"}))

    result = await SkillTool().execute({"name": "vcs"}, _ctx(tmp_path, sc))

    assert result.is_error is False
    assert reg.loaded_skills() == {"vcs"}


# --- bundle location (progressive disclosure of a skill's bundled reference files) ---
#
# A skill can reference bundled files by name from its body ("see `references/sample.md`"). The model
# reaches those with its ordinary ``read_file`` tool — but only if it knows where the bundle lives.
# So a skill load appends the bundle's base directory, expressed *relative to the working directory*
# (the file tool's root), mirroring Anthropic's "the skill's base directory path is automatically
# provided" contract. This is what makes materialised, worktree-confined bundles discoverable.


async def test_load_surfaces_the_bundle_location_relative_to_working_dir(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "canvas", body="THE PLAYBOOK BODY")
    sc = SkillContext(registry=_registry_with(path), available_tools=frozenset())

    result = await SkillTool().execute({"name": "canvas"}, _ctx(tmp_path, sc))

    assert result.is_error is False
    assert "THE PLAYBOOK BODY" in result.content  # the body is still loaded verbatim
    assert "canvas" in result.content  # the bundle's dir, relative to the working dir
    assert "read_file" in result.content  # how to reach a referenced file
    assert result.metadata["skill"] == "canvas"
    # the location leads the body: a large body is truncated/offloaded inline, so a trailing note would
    # be cut away — the location must sit in the head to survive.
    assert result.content.index("read_file") < result.content.index("THE PLAYBOOK BODY")


async def test_bundle_location_is_the_path_relative_to_the_working_dir(tmp_path: Path) -> None:
    """The surfaced path is worktree-relative — what ``read_file`` (rooted at working_dir) expects."""
    skills_root = tmp_path / ".harness" / "skills"
    skills_root.mkdir(parents=True)
    path = write_skill(skills_root, "canvas", body="B")
    sc = SkillContext(registry=_registry_with(path), available_tools=frozenset())

    result = await SkillTool().execute({"name": "canvas"}, _ctx(tmp_path, sc))

    assert result.is_error is False
    assert ".harness/skills/canvas" in result.content  # relative, not the absolute tmp path
    assert str(tmp_path) not in result.content


async def test_bundle_outside_the_working_dir_falls_back_to_absolute(tmp_path: Path) -> None:
    """When a skill's dir is not under the working dir, surface its absolute path (no crash)."""
    skills_root = tmp_path / "elsewhere"
    skills_root.mkdir()
    path = write_skill(skills_root, "canvas", body="B")
    working_dir = tmp_path / "worktree"
    working_dir.mkdir()
    sc = SkillContext(registry=_registry_with(path), available_tools=frozenset())

    result = await SkillTool().execute({"name": "canvas"}, _ctx(working_dir, sc))

    assert result.is_error is False
    assert str(path.parent) in result.content  # absolute fallback
