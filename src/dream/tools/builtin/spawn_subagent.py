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
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.observability._tracer import Tracer
from dream.subagents._declaration import SubagentSet
from dream.subagents._projection import SubagentResult, project_subagent
from dream.swarm._spawn import TeammateSpawnConfig
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

# Context metadata key for the SubagentSet
SUBAGENT_SET_CONTEXT_KEY = "dream.subagent_set"
# Context metadata key for the parent session id
PARENT_SESSION_KEY = "dream.parent_session_id"
# Context metadata key for parent tools
PARENT_TOOLS_KEY = "dream.parent_tools"
# Context metadata key for parent permissions
PARENT_PERMISSIONS_KEY = "dream.parent_permissions"
# Context metadata key for the team name
TEAM_KEY = "dream.team"
# Context metadata key for the tracer
TRACER_KEY = "dream.tracer"
# Context metadata key for the InProcessFactory (the callback that runs a subagent)
SUBAGENT_EXECUTOR_KEY = "dream.subagent_executor"

# V1 spawn cap per beat — cheap early guard; gate-2 (budget) is the cost backstop.
MAX_SPAWNS_PER_BEAT = 10


class SpawnSubagentInput(BaseModel):
    """Arguments for ``spawn_subagent``."""

    name: str = Field(
        description="Name of the subagent to dispatch. Must be one of the available subagents."
    )
    prompt: str = Field(
        description="The bounded task for the subagent. Be specific about what you need."
    )


class SpawnSubagentTool(BaseTool):
    """Dispatch a subagent to do bounded work and return its result."""

    name = "spawn_subagent"
    description = (
        "Spawn a capability-minimized subagent to perform a bounded task. "
        "The subagent runs to completion and returns its output text. "
        "Use this when you need to delegate focused, bounded work to a "
        "specialized teammate."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=300.0)
    input_model = SpawnSubagentInput

    def __init__(self) -> None:
        self._spawn_count: int = 0

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

        # --- Spawn cap guard ---
        self._spawn_count += 1
        if self._spawn_count > MAX_SPAWNS_PER_BEAT:
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

        # --- Retrieve context values ---
        parent_session_id = ctx.metadata.get(PARENT_SESSION_KEY, ctx.session_id)
        parent_tools: frozenset[str] = ctx.metadata.get(PARENT_TOOLS_KEY, frozenset())
        parent_permissions: tuple[str, ...] = ctx.metadata.get(PARENT_PERMISSIONS_KEY, ())
        team = ctx.metadata.get(TEAM_KEY, "default")
        executor = ctx.metadata.get(SUBAGENT_EXECUTOR_KEY)

        # --- Project Subagent → TeammateSpawnConfig ---
        config = project_subagent(
            agent,
            parent_session_id=parent_session_id,
            parent_tools=parent_tools,
            parent_permissions=parent_permissions,
            team=team,
            cwd=str(ctx.working_dir),
            prompt=args.prompt,
        )

        # --- Emit observability event: subagent.spawn ---
        tracer: Tracer | None = ctx.metadata.get(TRACER_KEY)
        spawn_time = time.time()
        if tracer is not None:
            tracer.event(
                "subagent.spawn",
                {
                    "subagent_name": args.name,
                    "prompt": args.prompt,
                    "depth": agent.depth,
                    "tools": list(agent.tools),
                    "model": agent.model or "parent_model",
                    "parent_session_id": parent_session_id,
                },
            )

        # --- Execute the subagent ---
        if executor is None:
            # No executor wired — run a simulated in-process execution
            result = await self._run_inline(config, agent, ctx)
        else:
            result = await self._run_with_executor(executor, config, agent, ctx)

        elapsed = time.time() - spawn_time

        # --- Emit observability event: subagent.complete ---
        if tracer is not None:
            tracer.event(
                "subagent.complete",
                {
                    "subagent_name": args.name,
                    "success": result.success,
                    "turns_used": result.turns_used,
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

        return ToolResult(
            content=result.output,
            is_error=False,
            metadata={
                "summary": f"subagent {args.name!r} completed in {result.turns_used} turns",
                "subagent_name": args.name,
                "turns_used": result.turns_used,
                "elapsed_seconds": round(elapsed, 2),
            },
        )

    async def _run_inline(
        self,
        config: TeammateSpawnConfig,
        agent: Any,
        ctx: ToolExecutionContext,
    ) -> SubagentResult:
        """Run the subagent inline using the InProcessExecutor pattern.

        When a full executor is not wired (e.g. in simple/test harnesses),
        this path runs the subagent's bounded work as a direct engine call
        within the parent's process. The subagent gets its own session with
        the capability-minimized toolset.
        """
        from dream.subagents._inline_executor import run_subagent_inline

        return await run_subagent_inline(config, agent, ctx)

    async def _run_with_executor(
        self,
        executor: Any,
        config: TeammateSpawnConfig,
        agent: Any,
        ctx: ToolExecutionContext,
    ) -> SubagentResult:
        """Run the subagent through the wired executor (InProcess/Subprocess)."""
        from dream.subagents._inline_executor import run_subagent_inline

        # V1: always use inline execution regardless of executor type.
        # The executor is available for future subprocess/remote backends.
        return await run_subagent_inline(config, agent, ctx)
