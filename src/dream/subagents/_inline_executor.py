"""Inline subagent executor — runs a subagent as a real bounded session.

The subagent gets its own ``Harness.run_role`` session with capability-
minimized tools and runs to completion bounded by ``max_turns``. It is a
real agent that can call ``read_file``, ``grep``, ``bash``, etc. — not a
single-shot LLM call.

Spec §09 v1: in-process, shared worktree, serial join, flat depth.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from dream.events import ToolUseResult, ToolUseStart
from dream.roles._manifest import RoleManifest
from dream.session import SessionOptions
from dream.subagents._declaration import MAX_SUBAGENT_DEPTH, Subagent, SubagentSet
from dream.subagents._output_guard import enforce_output_schema
from dream.subagents._projection import SubagentResult, intersect_tools

if TYPE_CHECKING:
    from dream.harness import Harness


async def run_subagent_inline(
    agent: Subagent,
    *,
    prompt: str,
    harness: Harness,
    parent_tools: frozenset[str] | None = None,
    spawn_counter: list[int] | None = None,
    tracer: object | None = None,
) -> SubagentResult:
    """Execute a subagent as a real bounded session with tools.

    Creates a synthetic ``RoleManifest`` scoped to the subagent's
    capability-minimized tools and runs it through ``harness.run_role()``. The
    subagent:

    - Gets a real engine session with actual tool dispatch
    - Has capability-minimized tools — ``agent.tools ∩ parent_tools`` (§05:
      narrower-wins; can only drop, never widen past the parent's allow-list).
      ``parent_tools is None`` means the parent had no role restriction, so the
      agent keeps its declared tools.
    - Cannot spawn sub-subagents (``spawn_subagent`` is disallowed)
    - Is bounded by ``agent.max_turns``
    - Returns plain text (concatenation of all assistant text deltas)
    """
    manifest = _build_subagent_manifest(agent, parent_tools=parent_tools)
    # Depth-2: an eligible spawner's child session carries a scoped set + the shared spawn counter
    # so it can dispatch its declared ``spawnable`` (the factory prefers these incoming keys). A leaf
    # gets ``{}`` → unchanged.
    child_metadata = build_child_spawn_metadata(
        agent,
        counter=spawn_counter if spawn_counter is not None else [0],
        harness=harness,
        tracer=tracer,
        parent_tools=parent_tools,
    )
    options = SessionOptions(max_turns=agent.max_turns, metadata=child_metadata)

    try:
        result = await harness.run_role(
            manifest,
            prompt,
            options=options,
        )
        # Count tool calls from the event stream for observability
        tool_calls = sum(
            1 for ev in result.events if isinstance(ev, ToolUseStart)
        )
        tool_errors = sum(
            1
            for ev in result.events
            if isinstance(ev, ToolUseResult) and ev.is_error
        )
        output, warning = result.final_text, None
        if agent.output_schema is not None:
            output, warning = await enforce_output_schema(
                result.final_text, agent=agent, harness=harness
            )
        return SubagentResult(
            name=agent.name,
            output=output,
            success=True,
            turns_used=tool_calls or 1,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            warning=warning,
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


def build_child_spawn_metadata(
    agent: Subagent,
    *,
    counter: list[int],
    harness: Harness | object,
    tracer: object | None,
    parent_tools: frozenset[str] | None,
) -> dict[str, Any]:
    """The ``SessionOptions.metadata`` a spawn-eligible child is handed (depth-2).

    Returns ``{}`` for a leaf (no seeding, unchanged). For an eligible spawner it carries a *scoped*
    subagent set (its declared ``spawnable``, one depth deeper, each tool-intersected with the
    spawner's own effective tools so a grandchild can only narrow) plus the *parent's* spawn
    ``counter`` (same object → the per-beat cap spans the whole tree), harness, tracer, and the
    spawner's effective tools as the grandchild's parent allow-list.
    """
    from dream.tools.builtin.spawn_subagent import (
        HARNESS_KEY,
        PARENT_TOOLS_KEY,
        SPAWN_COUNT_KEY,
        SUBAGENT_SET_CONTEXT_KEY,
        TRACER_KEY,
    )

    if not _can_spawn(agent):
        return {}

    effective_tools = intersect_tools(agent.tools, parent_tools)
    effective_frozen = frozenset(effective_tools)
    scoped = SubagentSet(
        agents={
            child.name: replace(
                child,
                depth=agent.depth + 1,
                tools=intersect_tools(child.tools, effective_frozen),
            )
            for child in agent.spawnable
        }
    )
    metadata: dict[str, Any] = {
        SUBAGENT_SET_CONTEXT_KEY: scoped,
        SPAWN_COUNT_KEY: counter,
        HARNESS_KEY: harness,
        PARENT_TOOLS_KEY: effective_frozen,
    }
    if tracer is not None:
        metadata[TRACER_KEY] = tracer
    return metadata


def _can_spawn(agent: Subagent) -> bool:
    """Whether this subagent may itself dispatch children — depth-2, bounded.

    Eligible = it declares ``spawnable`` children AND sits below the depth cap. A leaf (no
    ``spawnable``) or a grandchild at the cap is never eligible: ``spawn_subagent`` stays disallowed
    exactly as v1.
    """
    return bool(agent.spawnable) and agent.depth < MAX_SUBAGENT_DEPTH


def _build_subagent_manifest(
    agent: Subagent, *, parent_tools: frozenset[str] | None = None
) -> RoleManifest:
    """Build a synthetic RoleManifest for the subagent.

    Uses the ``generator`` role name (it needs tools) with the subagent's
    capability-minimized tool allow-list — ``agent.tools ∩ parent_tools`` (§05:
    narrower-wins, can only drop, never widen past the parent). ``spawn_subagent``
    is disallowed for a leaf; a spawn-eligible child (:func:`_can_spawn`) keeps it so it can
    dispatch its declared ``spawnable`` set (depth-2, bounded).
    """
    effective_tools = intersect_tools(agent.tools, parent_tools)
    can_spawn = _can_spawn(agent)

    spawn_note = (
        "You may dispatch your declared subagent(s) with spawn_subagent when it helps."
        if can_spawn
        else "You cannot spawn subagents yourself."
    )
    system_prompt = agent.system_prompt or (
        f"You are {agent.name}, a specialized subagent.\n\n"
        f"Role: {agent.description}\n\n"
        f"You are an ephemeral teammate spawned to do bounded work. "
        f"Complete the task described in the prompt using your available "
        f"tools, then return a clear, concise result. "
        f"{spawn_note}"
    )

    return RoleManifest(
        name="subagent",
        description=agent.description,
        system_prompt=system_prompt,
        system_prompt_mode="replace",
        tools=effective_tools,
        disallowed_tools=() if can_spawn else ("spawn_subagent",),
        skills=agent.skills,
        permission_mode="dontAsk",
        effort="medium",
    )
