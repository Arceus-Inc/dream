"""Inline subagent executor — runs a subagent as a real bounded session.

The subagent gets its own ``Harness.run_role`` session with capability-
minimized tools and runs to completion bounded by ``max_turns``. It is a
real agent that can call ``read_file``, ``grep``, ``bash``, etc. — not a
single-shot LLM call.

Spec §09 v1: in-process, shared worktree, serial join, flat depth.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from dream.events import ToolUseResult, ToolUseStart
from dream.roles._manifest import RoleManifest
from dream.session import SessionOptions
from dream.subagents._declaration import Subagent
from dream.subagents._projection import SubagentResult, intersect_tools

if TYPE_CHECKING:
    from dream.harness import Harness


async def run_subagent_inline(
    agent: Subagent,
    *,
    prompt: str,
    harness: Harness,
    parent_tools: frozenset[str] | None = None,
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
    options = SessionOptions(max_turns=agent.max_turns)

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
        return SubagentResult(
            name=agent.name,
            output=result.final_text,
            success=True,
            turns_used=tool_calls or 1,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
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


def _build_subagent_manifest(
    agent: Subagent, *, parent_tools: frozenset[str] | None = None
) -> RoleManifest:
    """Build a synthetic RoleManifest for the subagent.

    Uses the ``generator`` role name (it needs tools) with the subagent's
    capability-minimized tool allow-list — ``agent.tools ∩ parent_tools`` (§05:
    narrower-wins, can only drop, never widen past the parent). ``spawn_subagent``
    is always disallowed to prevent recursive spawning (v1 flat depth).
    """
    effective_tools = intersect_tools(agent.tools, parent_tools)

    system_prompt = agent.system_prompt or (
        f"You are {agent.name}, a specialized subagent.\n\n"
        f"Role: {agent.description}\n\n"
        f"You are an ephemeral teammate spawned to do bounded work. "
        f"Complete the task described in the prompt using your available "
        f"tools, then return a clear, concise result. "
        f"You cannot spawn subagents yourself."
    )

    return RoleManifest(
        name="subagent",
        description=agent.description,
        system_prompt=system_prompt,
        system_prompt_mode="replace",
        tools=effective_tools,
        disallowed_tools=("spawn_subagent",),
        skills=agent.skills,
        permission_mode="dontAsk",
        effort="medium",
    )
