"""Default ``spawn_subagent`` tool — dispatch a subagent from the parent beat.

Spec §04c: the agent-facing spawn tool.

spawn_subagent(name: str, prompt: str) -> SubagentResult
  # name  — must be in this beat's SubagentSet (else fail-closed)
  # prompt — the bounded task for the teammate
  # returns the teammate's final output, joined back into the parent turn

Why a tool, not a planner edge: the parent agent decides when to delegate,
mid-reasoning — so dispatch must be an action it can take (a tool call), not
a static decomposition.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.observability._tracer import Tracer
from dream.subagents._declaration import SubagentSet
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

if TYPE_CHECKING:
    from dream.harness import Harness

# ---------------------------------------------------------------------------
# Context metadata keys — typed constants instead of bare strings.
# ---------------------------------------------------------------------------
SUBAGENT_SET_CONTEXT_KEY = "dream.subagent_set"
# The run_role observer, stashed on the session so the spawn tool can forward it into a child
# session — that makes a NESTED spawn (depth-2) surface on the SAME observer/bus as the parent,
# instead of vanishing into the child's isolated per-session stream.
OBSERVER_KEY = "dream.observer"
PARENT_SESSION_KEY = "dream.parent_session_id"
PARENT_TOOLS_KEY = "dream.parent_tools"
PARENT_PERMISSIONS_KEY = "dream.parent_permissions"
TEAM_KEY = "dream.team"
TRACER_KEY = "dream.tracer"
HARNESS_KEY = "dream.harness"
# Per-session spawn counter — a mutable container ([int]) the factory seeds fresh
# per session in ``context_metadata`` (like the working-memory tier), so the cap
# is per-beat and resets naturally. NEVER instance state on the tool (the tool is
# a singleton in the harness-wide registry; instance state would accumulate across
# every session and become a permanent cross-session cap). See CONTRIBUTING.md
# "No module-level mutable state".
SPAWN_COUNT_KEY = "dream.subagent_spawn_count"

# V1 spawn cap per beat — cheap early guard; gate-2 (budget) is the cost backstop.
MAX_SPAWNS_PER_BEAT = 10


class SpawnSubagentInput(BaseModel):
    """Arguments for ``spawn_subagent``."""

    name: str = Field(
        description="Name of the subagent to dispatch. Must be one of the available subagents."
    )
    prompt: str = Field(
        default="",
        description=(
            "Bounded task for the subagent (legacy). Prefer ``goal`` + optional ``context`` "
            "for a Hermes-style context firewall."
        ),
    )
    goal: str | None = Field(
        default=None,
        description="Delegated task for a fresh-session specialist (preferred over prompt).",
    )
    context: str | None = Field(
        default=None,
        description="Packed extras for the child — not parent history. Artifact paths, contracts.",
    )
    background: bool = Field(
        default=False,
        description=(
            "If true, prefer keep-working join. When the async drain rail is unavailable, "
            "forced sync with a note (Hermes capacity fallback)."
        ),
    )


class SpawnSubagentTool(BaseTool):
    """Dispatch a subagent to do bounded work and return its result."""

    name = "spawn_subagent"
    description = (
        "Spawn a capability-minimized subagent to perform a bounded task. "
        "The subagent runs to completion with its own tool access and returns "
        "its output text. Use this when you need to delegate focused, bounded "
        "work to a specialized teammate."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=300.0)
    input_model = SpawnSubagentInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = SpawnSubagentInput.model_validate(input)

        # --- Retrieve subagent set from context ---
        subagent_set: SubagentSet | None = ctx.metadata.get(SUBAGENT_SET_CONTEXT_KEY)
        if subagent_set is None or not subagent_set:
            return ToolResult(
                content="No subagents are configured for this session.",
                is_error=True,
                metadata={
                    "root_cause": "no_subagent_set",
                    "safe_retry": "Subagents are not available. Complete the task yourself.",
                    "stop_condition": "Subagent dispatch is not supported in this session.",
                },
            )

        # --- Validate name is in the set (fail-closed) ---
        agent = subagent_set.get(args.name)
        if agent is None:
            available = subagent_set.names()
            return ToolResult(
                content=(f"Subagent {args.name!r} not found. Available subagents: {available}"),
                is_error=True,
                metadata={
                    "root_cause": f"unknown_subagent: {args.name}",
                    "safe_retry": f"Use one of the available subagents: {available}",
                    "stop_condition": "The requested subagent does not exist.",
                },
            )

        # --- Spawn cap guard (per-session, via context metadata) ---
        # The counter is a mutable container the factory seeds per session, so the
        # cap is per-beat. Default to a local [0] when absent (e.g. a bare test ctx)
        # rather than carrying state on the shared tool instance.
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

        # --- Retrieve harness from context ---
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
                    "safe_retry": "Pass goal=… (or prompt=…) describing the bounded task.",
                    "stop_condition": "No task text provided for the subagent.",
                },
            )

        # --- Emit observability event: subagent.spawn ---
        tracer: Tracer | None = ctx.metadata.get(TRACER_KEY)
        spawn_time = time.time()
        if tracer is not None:
            tracer.event(
                "subagent.spawn",
                {
                    "subagent_name": args.name,
                    "prompt": goal,
                    "depth": agent.depth,
                    "tools": list(agent.tools),
                    "model": agent.model or "parent_model",
                    "parent_session_id": ctx.session_id,
                },
            )

        # --- Execute: inline co-writers vs Hermes-style fresh-session delegate ---
        from dream.subagents._delegate import INLINE_SUBAGENTS, run_subagent_delegate
        from dream.subagents._inline_executor import run_subagent_inline

        parent_tools: frozenset[str] | None = ctx.metadata.get(PARENT_TOOLS_KEY)

        # Background keep-working: forced sync until completion drain ships (Hermes fallback).
        background_note = ""
        if args.background:
            background_note = (
                "\n\n[note: background=true forced sync — async drain rail not enabled; "
                "parent waited for this child.]"
            )

        if args.name in INLINE_SUBAGENTS:
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

        # --- Emit observability event: subagent.complete ---
        if tracer is not None:
            tracer.event(
                "subagent.complete",
                {
                    "subagent_name": args.name,
                    "success": result.success,
                    "turns_used": result.turns_used,
                    "tool_calls": result.tool_calls,
                    "tool_errors": result.tool_errors,
                    "elapsed_seconds": round(elapsed, 2),
                    "error": result.error,
                },
            )

        # --- Return result to parent ---
        if not result.success:
            return ToolResult(
                content=f"Subagent {args.name!r} failed: {result.error}",
                is_error=True,
                metadata={
                    "root_cause": f"subagent_failed: {result.error}",
                    "safe_retry": "Try rephrasing the task or do it yourself.",
                    "stop_condition": "The subagent could not complete the task.",
                    "subagent_name": args.name,
                    "elapsed_seconds": round(elapsed, 2),
                },
            )

        # Surface an output-schema guardrail warning inline, so the parent sees the contract was not
        # fully met (fail-open) rather than silently trusting a best-effort result.
        content = result.output + background_note
        if result.warning:
            content = f"{result.warning}\n\n{content}"
        return ToolResult(
            content=content,
            is_error=False,
            metadata={
                "summary": (
                    f"subagent {args.name!r} completed in {result.turns_used} turn(s), "
                    f"{result.tool_calls} tool call(s)"
                ),
                "subagent_name": args.name,
                "turns_used": result.turns_used,
                "tool_calls": result.tool_calls,
                "tool_errors": result.tool_errors,
                "elapsed_seconds": round(elapsed, 2),
                "output_warning": result.warning,
                "mode": "inline" if args.name in INLINE_SUBAGENTS else "delegate",
                "background_forced_sync": args.background,
            },
        )
