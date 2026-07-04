"""Default ``skill`` tool — load a skill playbook by name (Spec 06, Slice 2).

Read-only (tier 0, MUST #8): loading a body never mutates anything. The tool
reaches the per-session :class:`SkillRegistry` + available-tool set through the
``ToolExecutionContext.metadata`` channel (see ``dream.skills._session``).

Enforcement (diverges from the OpenHarness reference, which only checks
``disable_model_invocation`` and re-reads the registry from disk each call):

- ``disable_model_invocation`` skills are refused for the model (MUST #7).
- ``tools_required`` not all available → refused, no body loaded (MUST #6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.skills._session import read_skill_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err


def _bundle_location(base_dir: Path, working_dir: Path) -> str:
    """The skill's bundle dir as a path ``read_file`` can use — relative to ``working_dir`` when under
    it (the file tool's root), else the absolute path (a bundle outside the root is not read-confined)."""
    try:
        return str(base_dir.relative_to(working_dir))
    except ValueError:
        return str(base_dir)


def _with_bundle_footer(body: str, base_dir: Path, working_dir: Path) -> str:
    """Append the "where the bundled files live" footer that makes a skill's references discoverable.

    A skill body can name bundled files ("see ``references/sample.md``"); the model reaches them with
    its own ``read_file`` tool, but only once it knows the location. Surfacing the base dir mirrors
    Anthropic's Agent Skills contract ("the skill's base directory path is automatically provided").
    """
    location = _bundle_location(base_dir, working_dir)
    return (
        f"{body}\n\n---\n"
        f"This skill's bundled files (references, templates, examples) live in `{location}/`. "
        f"Read any file the body names — e.g. `read_file` on `{location}/template.md` — as needed; "
        "they are not yet in context."
    )


class SkillInput(BaseModel):
    """Arguments for the ``skill`` tool."""

    name: str = Field(description="Skill name (or command name / alias) to load.")


class SkillTool(BaseTool):
    """Load a skill playbook's body into context by name."""

    name = "skill"
    description = "Load a skill playbook by name to bring its instructions into context."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=10.0)
    input_model = SkillInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = SkillInput.model_validate(input)
        skill_ctx = read_skill_context(ctx.metadata)
        if skill_ctx is None:
            return _err(
                "Skills are not available in this session.",
                root_cause="no skill registry was wired into the execution context",
                safe_retry="run inside a session that enables skills",
                stop_condition="do not retry without skill wiring",
            )

        meta = skill_ctx.registry.resolve(args.name)
        if meta is None:
            return _err(
                f"Skill not found: {args.name}",
                root_cause=f"no skill named {args.name!r} is registered",
                safe_retry="list available skills and use an exact name",
                stop_condition="do not retry with the same name",
            )

        if meta.disable_model_invocation:
            command = meta.command_name or meta.name
            return _err(
                f"Skill {meta.name!r} can only be invoked by the user as /{command}.",
                root_cause="skill is operator-only (disable_model_invocation=true)",
                safe_retry="ask the operator to run it",
                stop_condition="do not retry this skill as the model",
            )

        missing = [t for t in meta.tools_required if t not in skill_ctx.available_tools]
        if missing:
            return _err(
                f"Skill {meta.name!r} requires unavailable tools: {missing}",
                root_cause=f"required tools are not in the registry: {missing}",
                safe_retry="enable the required tools, then load the skill again",
                stop_condition="do not retry until the required tools are available",
            )

        defn = skill_ctx.registry.use_skill(meta.name, event_sink=skill_ctx.event_sink)
        content = defn.content
        if meta.base_dir is not None:
            content = _with_bundle_footer(content, meta.base_dir, ctx.working_dir)
        return ToolResult(
            content=content,
            metadata={"skill": meta.name, "summary": f"loaded skill {meta.name!r}"},
        )


__all__ = ["SkillInput", "SkillTool"]
