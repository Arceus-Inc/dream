"""Default ``spawn_subagent`` tool — Cursor-style type enum + Hermes child isolation.

spawn_subagent(subagent_type, goal, context?) -> SubagentResult
  # subagent_type — enum: generalPurpose ∪ SubagentSet names (fail-closed)
  # goal — bounded task (alias: prompt); context — packed inlet, not parent history

``name`` is accepted as an alias for ``subagent_type`` for one release.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.observability._tracer import Tracer
from dream.subagents._declaration import Subagent, SubagentSet
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

if TYPE_CHECKING:
    from dream.harness import Harness

SUBAGENT_SET_CONTEXT_KEY = "dream.subagent_set"
OBSERVER_KEY = "dream.observer"
PARENT_SESSION_KEY = "dream.parent_session_id"
PARENT_TOOLS_KEY = "dream.parent_tools"
PARENT_PERMISSIONS_KEY = "dream.parent_permissions"
TEAM_KEY = "dream.team"
TRACER_KEY = "dream.tracer"
HARNESS_KEY = "dream.harness"
SPAWN_COUNT_KEY = "dream.subagent_spawn_count"

MAX_SPAWNS_PER_BEAT = 10
GENERAL_PURPOSE = "generalPurpose"

_DEFAULT_GP_TOOLS = (
    "read_file",
    "grep",
    "glob",
    "run_command",
    "write_file",
    "edit_file",
)


def spawn_type_names(subagent_set: SubagentSet | None) -> list[str]:
    """Enum values for this beat: generalPurpose first, then Spec names."""
    names = list(subagent_set.names()) if subagent_set else []
    return [GENERAL_PURPOSE, *names]


def general_purpose_agent(parent_tools: frozenset[str] | None) -> Subagent:
    """Hermes leaf worker — parent tools minus spawn (no nesting)."""
    if parent_tools is None:
        tools = _DEFAULT_GP_TOOLS
    else:
        tools = tuple(t for t in sorted(parent_tools) if t != "spawn_subagent")
    return Subagent(
        name=GENERAL_PURPOSE,
        description=(
            "Ad-hoc delegated worker. Fresh context; returns a summary. "
            "Use for reasoning-heavy subtasks that would flood the parent."
        ),
        tools=tools,
        max_turns=8,
    )


def build_spawn_parameters(
    base_schema: dict[str, Any],
    subagent_set: SubagentSet | None,
) -> dict[str, Any]:
    """Patch JSON schema with a dynamic subagent_type enum + short choice matrix."""
    types = spawn_type_names(subagent_set)
    catalog_lines = [
        f"- {GENERAL_PURPOSE}: ad-hoc fresh-context worker (summary only; no evidence gate)"
    ]
    if subagent_set:
        for name, desc in subagent_set.descriptions().items():
            short = desc.split("\n", 1)[0].strip()
            if len(short) > 120:
                short = short[:117] + "..."
            catalog_lines.append(f"- {name}: {short}")
    catalog = "\n".join(catalog_lines)

    schema = dict(base_schema)
    props = {k: dict(v) if isinstance(v, dict) else v for k, v in (schema.get("properties") or {}).items()}
    props["subagent_type"] = {
        "type": "string",
        "enum": types,
        "description": (
            "Which agent template to launch.\n\n"
            f"{catalog}\n\n"
            "WHEN TO USE: reasoning-heavy subtask, fresh/unbiased eyes, or a required specialist.\n"
            "WHEN NOT: single tool call — call it yourself. "
            "Evidence specialists must use their exact subagent_type (never forge their artifacts)."
        ),
    }
    # Prefer subagent_type; keep name for alias validation without requiring both.
    required = [r for r in (schema.get("required") or []) if r not in ("name", "subagent_type")]
    schema["properties"] = props
    schema["required"] = required
    return schema


class SpawnSubagentInput(BaseModel):
    """Arguments for ``spawn_subagent``."""

    subagent_type: str | None = Field(
        default=None,
        description="Agent template to launch (enum: generalPurpose + role specialists).",
    )
    name: str | None = Field(
        default=None,
        description="Deprecated alias for subagent_type.",
    )
    prompt: str = Field(
        default="",
        description="Legacy task text. Prefer goal.",
    )
    goal: str | None = Field(
        default=None,
        description="Delegated task for the child (preferred).",
    )
    context: str | None = Field(
        default=None,
        description="Packed extras for the child — not parent history.",
    )
    background: bool = Field(
        default=False,
        description=(
            "If true, prefer keep-working join. When the async drain rail is unavailable, "
            "forced sync with a note."
        ),
    )


class SpawnSubagentTool(BaseTool):
    """Dispatch a subagent to do bounded work and return its result."""

    name = "spawn_subagent"
    description = (
        "Spawn a focused subagent in an isolated context. "
        "Pick subagent_type from the enum (generalPurpose or a role specialist). "
        "Pass a self-contained goal (+ optional context). "
        "Parent sees only the summary — not the child's intermediate tool I/O. "
        "Use specialists when evidence/contracts require them; use generalPurpose for ad-hoc work."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=300.0)
    input_model = SpawnSubagentInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        from dream.subagents._delegate import INLINE_SUBAGENTS, run_subagent_delegate
        from dream.subagents._inline_executor import run_subagent_inline

        args = SpawnSubagentInput.model_validate(input)
        type_name = (args.subagent_type or args.name or "").strip()
        if not type_name:
            return ToolResult(
                content="spawn_subagent requires subagent_type (or name alias).",
                is_error=True,
                metadata={
                    "root_cause": "missing_subagent_type",
                    "safe_retry": "Pass subagent_type from the tool enum.",
                    "stop_condition": "No subagent_type provided.",
                },
            )

        subagent_set: SubagentSet | None = ctx.metadata.get(SUBAGENT_SET_CONTEXT_KEY)
        available = spawn_type_names(subagent_set)

        # Allow GP even when the Spec set is empty/missing (builtins-only session).
        if type_name != GENERAL_PURPOSE and (subagent_set is None or not subagent_set):
            return ToolResult(
                content=(
                    f"Subagent {type_name!r} not found. Available subagents: {available}"
                ),
                is_error=True,
                metadata={
                    "root_cause": f"unknown_subagent: {type_name}",
                    "safe_retry": f"Use one of: {available}",
                    "stop_condition": "The requested subagent does not exist.",
                },
            )

        parent_tools: frozenset[str] | None = ctx.metadata.get(PARENT_TOOLS_KEY)
        if type_name == GENERAL_PURPOSE:
            agent = general_purpose_agent(parent_tools)
        else:
            assert subagent_set is not None
            agent = subagent_set.get(type_name)
            if agent is None:
                return ToolResult(
                    content=(
                        f"Subagent {type_name!r} not found. Available subagents: {available}"
                    ),
                    is_error=True,
                    metadata={
                        "root_cause": f"unknown_subagent: {type_name}",
                        "safe_retry": f"Use one of: {available}",
                        "stop_condition": "The requested subagent does not exist.",
                    },
                )

        counter: list[int] | None = ctx.metadata.get(SPAWN_COUNT_KEY)
        if counter is None:
            counter = [0]
            ctx.metadata[SPAWN_COUNT_KEY] = counter
        counter[0] += 1
        if counter[0] > MAX_SPAWNS_PER_BEAT:
            return ToolResult(
                content=(
                    f"Spawn cap exceeded ({MAX_SPAWNS_PER_BEAT} per beat). "
                    f"Complete remaining work yourself."
                ),
                is_error=True,
                metadata={
                    "root_cause": "spawn_cap_exceeded",
                    "safe_retry": "Do the work directly instead of delegating.",
                    "stop_condition": f"Max {MAX_SPAWNS_PER_BEAT} subagent spawns per beat.",
                },
            )

        harness: Harness | None = ctx.metadata.get(HARNESS_KEY)
        if harness is None:
            return ToolResult(
                content="No harness wired for subagent execution.",
                is_error=True,
                metadata={
                    "root_cause": "no_harness",
                    "safe_retry": "Subagent execution requires a wired harness.",
                    "stop_condition": "Cannot spawn subagents without a harness.",
                },
            )

        goal = (args.goal or args.prompt or "").strip()
        if not goal:
            return ToolResult(
                content="spawn_subagent requires goal or prompt.",
                is_error=True,
                metadata={
                    "root_cause": "missing_goal",
                    "safe_retry": "Pass goal=… describing the bounded task.",
                    "stop_condition": "No task text provided for the subagent.",
                },
            )

        tracer: Tracer | None = ctx.metadata.get(TRACER_KEY)
        spawn_time = time.time()
        if tracer is not None:
            tracer.event(
                "subagent.spawn",
                {
                    "subagent_name": type_name,
                    "subagent_type": type_name,
                    "prompt": goal,
                    "depth": agent.depth,
                    "tools": list(agent.tools),
                    "model": agent.model or "parent_model",
                    "parent_session_id": ctx.session_id,
                },
            )

        background_note = ""
        if args.background:
            background_note = (
                "\n\n[note: background=true forced sync — async drain rail not enabled; "
                "parent waited for this child.]"
            )

        mode = "inline" if type_name in INLINE_SUBAGENTS else "delegate"
        if mode == "inline":
            result = await run_subagent_inline(
                agent,
                prompt=goal if not args.context else f"{goal}\n\nCONTEXT:\n{args.context}",
                harness=harness,
                parent_tools=parent_tools,
                spawn_counter=counter,
                tracer=tracer,
                observer=ctx.metadata.get(OBSERVER_KEY),
            )
        else:
            result = await run_subagent_delegate(
                agent,
                goal=goal,
                context=args.context,
                harness=harness,
                parent_tools=parent_tools,
                spawn_counter=counter,
                tracer=tracer,
                observer=ctx.metadata.get(OBSERVER_KEY),
                working_dir=ctx.working_dir,
            )

        elapsed = time.time() - spawn_time
        if tracer is not None:
            tracer.event(
                "subagent.complete",
                {
                    "subagent_name": type_name,
                    "subagent_type": type_name,
                    "success": result.success,
                    "turns_used": result.turns_used,
                    "tool_calls": result.tool_calls,
                    "tool_errors": result.tool_errors,
                    "elapsed_seconds": round(elapsed, 2),
                    "error": result.error,
                },
            )

        if not result.success:
            return ToolResult(
                content=f"Subagent {type_name!r} failed: {result.error}",
                is_error=True,
                metadata={
                    "root_cause": f"subagent_failed: {result.error}",
                    "safe_retry": "Try rephrasing the task or do it yourself.",
                    "stop_condition": "The subagent could not complete the task.",
                    "subagent_name": type_name,
                    "elapsed_seconds": round(elapsed, 2),
                },
            )

        content = result.output + background_note
        if result.warning:
            content = f"{result.warning}\n\n{content}"
        return ToolResult(
            content=content,
            is_error=False,
            metadata={
                "summary": (
                    f"subagent {type_name!r} completed in {result.turns_used} turn(s), "
                    f"{result.tool_calls} tool call(s)"
                ),
                "subagent_name": type_name,
                "subagent_type": type_name,
                "turns_used": result.turns_used,
                "tool_calls": result.tool_calls,
                "tool_errors": result.tool_errors,
                "elapsed_seconds": round(elapsed, 2),
                "output_warning": result.warning,
                "mode": mode,
                "background_forced_sync": args.background,
                "artifacts": [],
                "next_actions": (),
            },
        )
