"""Inline subagent executor — runs a subagent within the parent process.

V1 profile: the reasoner. In-process, shared parent worktree, serial join.
The subagent gets its own session with capability-minimized tools and runs
to completion bounded by max_turns.

Spec §09 v1: in-process, shared worktree, read parent scope, write nothing,
plain text result, flat depth, serial join, per-beat spawn-count cap.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dream.subagents._declaration import Subagent
from dream.subagents._projection import SubagentResult
from dream.swarm._spawn import TeammateSpawnConfig
from dream.tools._context import ToolExecutionContext


async def run_subagent_inline(
    config: TeammateSpawnConfig,
    agent: Subagent,
    ctx: ToolExecutionContext,
) -> SubagentResult:
    """Execute a subagent inline within the parent's process.

    This is the v1 reasoner profile: the subagent runs as a bounded agent
    loop using the parent's engine factory (if available) or a simplified
    prompt-response cycle. The result is plain text joined back into the
    parent turn.

    The subagent:
    - Has its own turn budget (agent.max_turns)
    - Cannot spawn sub-subagents (fail-closed; spawn tool not in its toolset)
    - Reads the parent's worktree but writes nothing persistent
    - Returns plain text output
    """
    # Try to get the engine factory from context for a full agent loop
    engine_factory = ctx.metadata.get("dream.engine_factory")

    if engine_factory is not None:
        return await _run_with_engine(config, agent, ctx, engine_factory)

    # Fallback: use the LLM directly for a single-turn response
    llm_callable = ctx.metadata.get("dream.llm_callable")
    if llm_callable is not None:
        return await _run_with_llm(config, agent, ctx, llm_callable)

    # No engine or LLM available — return a structured error
    return SubagentResult(
        name=agent.name,
        output="",
        success=False,
        error="No engine or LLM callable wired for subagent execution",
        turns_used=0,
    )


async def _run_with_engine(
    config: TeammateSpawnConfig,
    agent: Subagent,
    ctx: ToolExecutionContext,
    engine_factory: Any,
) -> SubagentResult:
    """Run the subagent through a full engine loop."""
    try:
        # The engine factory creates a bounded session for the subagent
        result_text = await engine_factory(config)
        return SubagentResult(
            name=agent.name,
            output=result_text if isinstance(result_text, str) else str(result_text),
            success=True,
            turns_used=1,  # simplified; real impl tracks turns
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return SubagentResult(
            name=agent.name,
            output="",
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            turns_used=0,
        )


async def _run_with_llm(
    config: TeammateSpawnConfig,
    agent: Subagent,
    ctx: ToolExecutionContext,
    llm_callable: Any,
) -> SubagentResult:
    """Run the subagent as a direct LLM call (simplified single-turn)."""
    try:
        # Build the messages for the subagent
        messages = [
            {"role": "system", "content": config.system_prompt or ""},
            {"role": "user", "content": config.prompt},
        ]

        # Call the LLM
        response = await llm_callable(
            messages=messages,
            model=config.model,
        )
        output = response if isinstance(response, str) else str(response)

        return SubagentResult(
            name=agent.name,
            output=output,
            success=True,
            turns_used=1,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return SubagentResult(
            name=agent.name,
            output="",
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            turns_used=0,
        )
