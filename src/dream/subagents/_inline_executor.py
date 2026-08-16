"""Delegated subagent session executor — runs a subagent as a real bounded session.

Live path: capability-minimized ``Harness.run_role`` session bounded by
``max_turns``. Shared worktree by default; optional short-lived git worktree
when ``IsolationMode.WORKTREE``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from dream.api.response_format import resolve_structured_output
from dream.events import ToolUseResult, ToolUseStart
from dream.roles._manifest import RoleManifest
from dream.session import SessionOptions
from dream.subagents._declaration import MAX_INLINE_NESTING, Subagent, SubagentSet
from dream.subagents._host_blocklist import strip_host_blocked, strip_unconfinable_commands
from dream.subagents._isolation import IsolationMode
from dream.subagents._output_guard import enforce_output_schema
from dream.subagents._projection import SubagentResult, intersect_tools
from dream.subagents._worktree import SubagentWorktree, SubagentWorktreeFactory

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.runner.events import RunTaskObserver


SUBAGENT_NAME_METADATA_KEY = "dream.subagent_name"
SUBAGENT_OVERLAY_METADATA_KEY = "dream.subagent_permission_overlay"
SUBAGENT_WORKING_DIR_METADATA_KEY = "dream.subagent_working_dir"


async def run_subagent_session(
    agent: Subagent,
    *,
    prompt: str,
    harness: Harness,
    parent_tools: frozenset[str] | None = None,
    spawn_counter: list[int] | None = None,
    tracer: object | None = None,
    observer: RunTaskObserver | None = None,
    working_dir: Path | None = None,
    spill_dir: Path | None = None,
    goal: str | None = None,
    context: str | None = None,
) -> SubagentResult:
    """Execute a subagent as a real bounded session with tools."""
    from dream.subagents._delegate import build_child_prompt

    worktree: SubagentWorktree | None = None
    child_cwd = working_dir
    effective_prompt = prompt
    try:
        if agent.isolation is IsolationMode.WORKTREE:
            if spill_dir is None or working_dir is None:
                return SubagentResult(
                    name=agent.name,
                    output="",
                    success=False,
                    error=(
                        "IsolationMode.WORKTREE requires parent working_dir and session scratch_dir"
                    ),
                    turns_used=0,
                )
            factory = SubagentWorktreeFactory(
                scratch_dir=spill_dir,
                parent_cwd=working_dir,
            )
            worktree = factory.create(agent.name)
            child_cwd = worktree.path
            if goal is not None:
                effective_prompt = build_child_prompt(
                    goal,
                    context,
                    workspace_path=str(child_cwd),
                    ephemeral_workspace=True,
                )

        manifest = _build_subagent_manifest(agent, parent_tools=parent_tools)
        child_metadata = build_child_spawn_metadata(
            agent,
            counter=spawn_counter if spawn_counter is not None else [0],
            harness=harness,
            tracer=tracer,
            parent_tools=parent_tools,
        )
        child_metadata[SUBAGENT_NAME_METADATA_KEY] = agent.name
        if agent.permission_overlay:
            child_metadata[SUBAGENT_OVERLAY_METADATA_KEY] = agent.permission_overlay
        if child_cwd is not None:
            child_metadata[SUBAGENT_WORKING_DIR_METADATA_KEY] = child_cwd

        response_format = None
        if agent.output_schema is not None:
            response_format = resolve_structured_output(
                schema=agent.output_schema,
                name=f"{agent.name}_output",
                strict=agent.strict,
            )
        options = SessionOptions(
            model=agent.model,
            max_turns=agent.max_turns,
            response_format=response_format,
            metadata=child_metadata,
        )

        result = await harness.run_role(
            manifest,
            effective_prompt,
            options=options,
            observer=observer,
        )
        tool_calls = sum(1 for ev in result.events if isinstance(ev, ToolUseStart))
        tool_errors = sum(
            1 for ev in result.events if isinstance(ev, ToolUseResult) and ev.is_error
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
    finally:
        if worktree is not None:
            with contextlib.suppress(Exception):
                worktree.remove()


def build_child_spawn_metadata(
    agent: Subagent,
    *,
    counter: list[int],
    harness: Harness | object,
    tracer: object | None,
    parent_tools: frozenset[str] | None,
) -> dict[str, object]:
    """Session metadata for a spawn-eligible child (depth-2). Leaves get ``{}``."""
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
    metadata: dict[str, object] = {
        SUBAGENT_SET_CONTEXT_KEY: scoped,
        SPAWN_COUNT_KEY: counter,
        HARNESS_KEY: harness,
        PARENT_TOOLS_KEY: effective_frozen,
    }
    if tracer is not None:
        metadata[TRACER_KEY] = tracer
    return metadata


def _can_spawn(agent: Subagent) -> bool:
    return bool(agent.spawnable) and agent.depth < MAX_INLINE_NESTING


def _build_subagent_manifest(
    agent: Subagent, *, parent_tools: frozenset[str] | None = None
) -> RoleManifest:
    """Synthetic RoleManifest: tools ∩ parent, host blocklist, spawn allow/deny."""
    effective_tools = intersect_tools(agent.tools, parent_tools)
    can_spawn = _can_spawn(agent)
    effective_tools = strip_host_blocked(effective_tools, keep_spawn=can_spawn)
    if agent.isolation is IsolationMode.WORKTREE:
        effective_tools = strip_unconfinable_commands(effective_tools)

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
