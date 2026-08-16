"""Harness-builtin subagent templates (OpenHarness Explore / Plan / verification).

Merged into every beat that enables ``spawn_subagent``. Role specialists add
names; they do not remove these builtins. Fail-closed enum = builtins | role set.
"""

from __future__ import annotations

from dream.api.response_format import JsonSchema
from dream.subagents._declaration import (
    GENERAL_PURPOSE_NAME,
    Subagent,
    SubagentSet,
)
from dream.subagents._host_blocklist import EXPLORE_TOOLS, PLAN_TOOLS, VERIFY_TOOLS
from dream.subagents._isolation import IsolationMode

EXPLORE = "explore"
PLAN = "plan"
VERIFY = "verify"
GENERAL_PURPOSE = GENERAL_PURPOSE_NAME

_VERIFY_SCHEMA = JsonSchema.of(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "summary", "findings"],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["PASS", "FAIL", "PARTIAL"],
                "description": "Machine-readable verification outcome.",
            },
            "summary": {
                "type": "string",
                "description": "One-paragraph rationale.",
            },
            "findings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete defects or evidence lines.",
            },
        },
    }
)


def explore_agent() -> Subagent:
    """Read-only mapper — OpenHarness Explore denylist as Dream allowlist."""
    return Subagent(
        name=EXPLORE,
        description=(
            "Read-only explore: map the codebase or gather evidence. "
            "Cannot edit files, run mutating shell, or spawn children. "
            "Return Critical Files, findings, and open questions."
        ),
        tools=EXPLORE_TOOLS,
        max_turns=12,
        isolation=IsolationMode.SHARED,
        system_prompt=(
            "You are explore, a read-only research subagent.\n"
            "Map only what the goal asks for. Do not edit files or mutate state.\n"
            "End with: Critical Files, Findings, Open Questions."
        ),
    )


def plan_agent() -> Subagent:
    """Read-only planner — no mutations (OpenHarness Plan)."""
    return Subagent(
        name=PLAN,
        description=(
            "Read-only planner: produce a concrete implementation plan. "
            "Cannot edit files or mutate the repo. Return ordered steps and risks."
        ),
        tools=PLAN_TOOLS,
        max_turns=10,
        isolation=IsolationMode.SHARED,
        system_prompt=(
            "You are plan, a read-only planning subagent.\n"
            "Produce an actionable plan with ordered steps, files touched, and risks.\n"
            "Do not edit files or run mutating commands."
        ),
    )


def verify_agent() -> Subagent:
    """Strict PASS/FAIL verifier — OpenHarness verification verdict contract."""
    return Subagent(
        name=VERIFY,
        description=(
            "Blind verifier: judge evidence against a contract. "
            "Returns strict JSON with verdict PASS|FAIL|PARTIAL. Prefer when isolation "
            "from the author's context matters."
        ),
        tools=VERIFY_TOOLS,
        max_turns=10,
        isolation=IsolationMode.SHARED,
        output_schema=_VERIFY_SCHEMA,
        strict=True,
        system_prompt=(
            "You are verify, an adversarial grader.\n"
            "Judge only against the stated contract and evidence you can observe.\n"
            "Final message MUST be JSON matching the schema with "
            "verdict PASS|FAIL|PARTIAL."
        ),
    )


def builtin_agents() -> tuple[Subagent, ...]:
    """The harness catalog always offered when spawn is enabled."""
    return (explore_agent(), plan_agent(), verify_agent())


def merge_builtins(role_set: SubagentSet | None) -> SubagentSet:
    """Role agents overlay builtins; role wins on name collision."""
    agents: dict[str, Subagent] = {agent.name: agent for agent in builtin_agents()}
    if role_set is not None:
        for name, agent in role_set.agents.items():
            agents[name] = agent
    return SubagentSet(agents=agents)


def spawn_catalog_names(role_set: SubagentSet | None) -> tuple[str, ...]:
    """Enum values: generalPurpose, builtins, then remaining role names."""
    merged = merge_builtins(role_set)
    names = [GENERAL_PURPOSE, EXPLORE, PLAN, VERIFY]
    for name in merged.names():
        if name not in names:
            names.append(name)
    return tuple(names)


__all__ = [
    "EXPLORE",
    "GENERAL_PURPOSE",
    "PLAN",
    "VERIFY",
    "builtin_agents",
    "explore_agent",
    "merge_builtins",
    "plan_agent",
    "spawn_catalog_names",
    "verify_agent",
]
