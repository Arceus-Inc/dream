"""Chorus → dream projection: Subagent → TeammateSpawnConfig.

The factory resolves a role's Tier-1 Subagents + any referenced Tier-2 registry
entries, intersects each against the parent's live toolset/permissions (§05:
narrower-wins), and emits a TeammateSpawnConfig template per subagent.

Spec §04b: the factory projects chorus → dream.
"""

from __future__ import annotations

from dataclasses import dataclass

from dream.subagents._declaration import Subagent, SubagentSet
from dream.swarm._spawn import TeammateSpawnConfig


@dataclass(frozen=True)
class SubagentResult:
    """Result returned by a subagent dispatch.

    The parent folds this into its reasoning. Plain text in v1 (the consumer
    is the parent LLM; text is native). Typed results arrive with the fan-out
    seam.
    """

    name: str
    output: str
    success: bool = True
    error: str | None = None
    turns_used: int = 0
    tool_calls: int = 0
    tool_errors: int = 0


def project_subagent(
    agent: Subagent,
    *,
    parent_session_id: str,
    parent_tools: frozenset[str],
    parent_permissions: tuple[str, ...],
    team: str,
    cwd: str,
    prompt: str,
) -> TeammateSpawnConfig:
    """Project a chorus Subagent declaration into a dream TeammateSpawnConfig.

    Applies capability minimization (§05):
    - tools: agent.tools ∩ parent_tools (strict subset)
    - permissions: parent_permissions minus agent.permission_overlay (tighten-only)

    The prompt is the bounded task text from the dispatch call.
    """
    # Permission overlay: tighten-only — remove specified permissions
    minimized_permissions = tuple(
        p for p in parent_permissions if p not in agent.permission_overlay
    )

    # Build system prompt from description if not explicitly set
    system_prompt = agent.system_prompt
    if system_prompt is None:
        system_prompt = (
            f"You are {agent.name}, a specialized subagent.\n\n"
            f"Role: {agent.description}\n\n"
            f"You are an ephemeral teammate spawned to do bounded work. "
            f"Complete the task described in the prompt and return a clear, "
            f"concise result. You cannot spawn subagents yourself."
        )

    return TeammateSpawnConfig(
        name=agent.name,
        team=team,
        prompt=prompt,
        cwd=cwd,
        parent_session_id=parent_session_id,
        depth=agent.depth,
        model=agent.model,
        system_prompt=system_prompt,
        system_prompt_mode="replace",
        permissions=minimized_permissions,
        plan_mode_required=False,
        allow_permission_prompts=False,
        task_type="in_process_teammate",
    )


def build_subagent_set(
    *,
    tier1_agents: list[Subagent] | None = None,
    tier2_agents: list[Subagent] | None = None,
    parent_tools: frozenset[str] | None = None,
) -> SubagentSet:
    """Build the resolved SubagentSet for a beat.

    Merges Tier-1 (role-owned) and Tier-2 (shared registry) subagents.
    Validates that each subagent's tools are achievable given the parent's
    toolset. If parent_tools is None, no intersection is performed (used
    when the full tool surface is unknown at build time).
    """
    agents: dict[str, Subagent] = {}

    for agent_list in (tier1_agents or [], tier2_agents or []):
        for agent in agent_list:
            if agent.name in agents:
                raise ValueError(f"Duplicate subagent name {agent.name!r} in Tier-1 and Tier-2")
            agents[agent.name] = agent

    return SubagentSet(agents=agents)
