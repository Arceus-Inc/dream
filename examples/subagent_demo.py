"""Demo: subagent system end-to-end — a GTM marketer with a researcher subagent.

What this does
--------------
Demonstrates the chorus subagent layer:

1. Declares a Tier-1 role-owned subagent ("researcher") on a GTM engineer role.
2. Declares a Tier-2 shared subagent ("copy_editor") in the SubagentRegistry.
3. Builds a SubagentSet, projects them, and wires them into the harness.
4. The parent beat can dispatch subagents via the ``spawn_subagent`` tool.
5. Observability traces (JSONL) capture the full lifecycle.

Configuration
-------------
Uses the same env vars as run_task_demo.py:

    DREAM_SMOKE_API_KEY   = <your key>
    DREAM_SMOKE_MODEL     = <model / Azure deployment name>
    DREAM_SMOKE_BASE_URL  = <OpenAI-compatible base URL incl. /v1>

Or set AZURE_OPENAI_API_KEY / AZURE_OPENAI_BASE_URL / AZURE_OPENAI_DEPLOYMENT.

Run it
------
    python examples/subagent_demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Allow running from the repo root or examples dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dream.subagents import (
    Subagent,
    SubagentRegistry,
    SubagentResult,
    SubagentSet,
    project_subagent,
)
from dream.subagents._projection import build_subagent_set


def _load_env() -> dict[str, str]:
    """Load credentials from environment."""
    # Try Azure env vars first, then DREAM_SMOKE_* vars
    if os.environ.get("AZURE_OPENAI_API_KEY"):
        return {
            "api_key": os.environ["AZURE_OPENAI_API_KEY"],
            "base_url": os.environ.get("AZURE_OPENAI_BASE_URL", ""),
            "model": os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        }
    return {
        "api_key": os.environ.get("DREAM_SMOKE_API_KEY", ""),
        "base_url": os.environ.get("DREAM_SMOKE_BASE_URL", ""),
        "model": os.environ.get("DREAM_SMOKE_MODEL", ""),
    }


def demo_data_model() -> None:
    """Demonstrate the Subagent data model and registry."""
    print("=" * 60)
    print("§01 — SUBAGENT DATA MODEL")
    print("=" * 60)

    # --- Tier-1: Role-owned subagent ---
    researcher = Subagent(
        name="researcher",
        description=(
            "Researches market data, competitor intel, and audience insights. "
            "Returns structured findings the parent uses to craft campaigns."
        ),
        tools=("read_file", "grep", "bash"),
        skills=("market_research",),
        depth=1,
        model=None,  # inherits parent model
        max_turns=6,
    )
    print(f"\n  Tier-1 (role-owned): {researcher.name}")
    print(f"    description: {researcher.description[:60]}...")
    print(f"    tools: {researcher.tools}")
    print(f"    depth: {researcher.depth}")
    print(f"    max_turns: {researcher.max_turns}")

    # --- Tier-2: Shared capability agent ---
    copy_editor = Subagent(
        name="copy_editor",
        description=(
            "Polishes and tightens marketing copy. Checks tone, grammar, "
            "and brand voice consistency."
        ),
        tools=("read_file",),
        depth=1,
        model="gpt-4o-mini",  # cheaper model for editing
        max_turns=4,
    )
    print(f"\n  Tier-2 (shared): {copy_editor.name}")
    print(f"    description: {copy_editor.description[:60]}...")
    print(f"    tools: {copy_editor.tools}")
    print(f"    model: {copy_editor.model} (cheaper for editing tasks)")

    # --- Registry ---
    print("\n" + "-" * 40)
    print("  SubagentRegistry (Tier-2 shared)")
    registry = SubagentRegistry()
    registry.register(copy_editor)
    print(f"    registered: {registry.list_names()}")
    print(f"    lookup 'copy_editor': {registry.get('copy_editor') is not None}")

    # --- SubagentSet (built at beat time) ---
    print("\n" + "-" * 40)
    print("  SubagentSet (resolved for a beat)")
    agent_set = build_subagent_set(
        tier1_agents=[researcher],
        tier2_agents=registry.resolve(("copy_editor",)),
    )
    print(f"    names: {agent_set.names()}")
    print(f"    descriptions: {json.dumps(agent_set.descriptions(), indent=6)}")
    print(f"    contains 'researcher': {'researcher' in agent_set}")
    print(f"    contains 'unknown': {'unknown' in agent_set}")

    return researcher, copy_editor, agent_set


def demo_projection(agent_set: SubagentSet) -> None:
    """Demonstrate the chorus → dream projection."""
    print("\n" + "=" * 60)
    print("§02 — PROJECTION (Subagent → TeammateSpawnConfig)")
    print("=" * 60)

    agent = agent_set.get("researcher")

    # Simulate parent context
    parent_tools = frozenset({"read_file", "grep", "bash", "git", "write_file"})
    parent_permissions = ("read", "write", "execute", "network")

    config = project_subagent(
        agent,
        parent_session_id="beat-session-abc123",
        parent_tools=parent_tools,
        parent_permissions=parent_permissions,
        team="gtm-team",
        cwd="/workspace/campaign-q3",
        prompt="Research the top 3 competitors in the AI agent space and summarize their GTM strategies.",
    )

    print(f"\n  TeammateSpawnConfig:")
    print(f"    name: {config.name}")
    print(f"    team: {config.team}")
    print(f"    depth: {config.depth}")
    print(f"    parent_session_id: {config.parent_session_id}")
    print(f"    model: {config.model or '(inherits parent)'}")
    print(f"    allow_permission_prompts: {config.allow_permission_prompts}")
    print(f"    system_prompt_mode: {config.system_prompt_mode}")
    print(f"    task_type: {config.task_type}")
    print(f"    prompt: {config.prompt[:60]}...")
    print(f"\n  Capability Minimization (§05):")
    print(f"    parent tools: {sorted(parent_tools)}")
    print(f"    agent declared: {agent.tools}")
    print(f"    → intersected: only tools in BOTH sets pass through")
    print(f"\n  Permission Overlay (tighten-only):")
    print(f"    parent perms: {parent_permissions}")
    print(f"    overlay drops: {agent.permission_overlay or '(none)'}")
    print(f"    → result: {config.permissions}")


def demo_spawn_tool_simulation(agent_set: SubagentSet) -> None:
    """Demonstrate the spawn_subagent tool execution flow."""
    print("\n" + "=" * 60)
    print("§03 — SPAWN TOOL (agent-facing tool call)")
    print("=" * 60)

    from dream.tools._context import ToolExecutionContext
    from dream.tools.builtin.spawn_subagent import (
        PARENT_PERMISSIONS_KEY,
        PARENT_SESSION_KEY,
        PARENT_TOOLS_KEY,
        SUBAGENT_SET_CONTEXT_KEY,
        TEAM_KEY,
        SpawnSubagentTool,
    )

    tool = SpawnSubagentTool()
    print(f"\n  Tool: {tool.name}")
    print(f"  Description: {tool.description[:80]}...")
    print(f"  Risk: {tool.declaration.risk}")
    print(f"  Tier required: {tool.declaration.tier_required}")

    # Build context with a mock LLM
    async def mock_llm(messages: list[dict], model: str | None = None) -> str:
        # Simulate what a subagent would produce
        system = messages[0]["content"] if messages else ""
        user_prompt = messages[1]["content"] if len(messages) > 1 else ""
        return (
            f"## Research Findings\n\n"
            f"Based on my analysis:\n\n"
            f"1. **Competitor A** — Product-led growth with freemium tier\n"
            f"2. **Competitor B** — Enterprise sales with partner channel\n"
            f"3. **Competitor C** — Community-driven open-source strategy\n\n"
            f"Key insight: The market is splitting between PLG and enterprise."
        )

    ctx = ToolExecutionContext(
        working_dir=Path("/tmp/demo"),
        session_id="parent-beat-session",
        metadata={
            SUBAGENT_SET_CONTEXT_KEY: agent_set,
            PARENT_SESSION_KEY: "parent-beat-session",
            PARENT_TOOLS_KEY: frozenset({"read_file", "grep", "bash", "git"}),
            PARENT_PERMISSIONS_KEY: ("read", "write"),
            TEAM_KEY: "gtm-team",
            "dream.llm_callable": mock_llm,
        },
    )

    async def run_tool():
        # Test 1: Valid spawn
        print("\n  --- Dispatch: spawn_subagent('researcher', <prompt>) ---")
        result = await tool.execute(
            {
                "name": "researcher",
                "prompt": "Research the top 3 competitors in the AI agent space.",
            },
            ctx,
        )
        print(f"  Success: {not result.is_error}")
        print(f"  Metadata: {json.dumps(result.metadata, indent=4)}")
        print(f"  Output (first 200 chars):\n    {result.content[:200]}")

        # Test 2: Invalid name (fail-closed)
        print("\n  --- Dispatch: spawn_subagent('nonexistent', ...) ---")
        result2 = await tool.execute(
            {"name": "nonexistent", "prompt": "do stuff"},
            ctx,
        )
        print(f"  Error (fail-closed): {result2.is_error}")
        print(f"  Message: {result2.content[:100]}")

    asyncio.run(run_tool())


def demo_observability() -> None:
    """Demonstrate the observability trace integration."""
    print("\n" + "=" * 60)
    print("§04 — OBSERVABILITY (trace events)")
    print("=" * 60)

    print("\n  The spawn_subagent tool emits two trace events:")
    print("    1. subagent.spawn  — when dispatch begins")
    print("       attributes: subagent_name, prompt, depth, tools, model, parent_session_id")
    print("    2. subagent.complete — when the subagent finishes")
    print("       attributes: subagent_name, success, turns_used, elapsed_seconds, error")
    print("\n  These appear in the JSONL trace alongside llm.call / tool.call events.")
    print("  Query them with: query_logs(filter='subagent.*')")

    # Show what a trace event looks like
    sample_event = {
        "type": "subagent.spawn",
        "timestamp": "2025-01-15T10:30:00Z",
        "session_id": "beat-abc123",
        "attributes": {
            "subagent_name": "researcher",
            "prompt": "Research top 3 competitors...",
            "depth": 1,
            "tools": ["read_file", "grep", "bash"],
            "model": "parent_model",
            "parent_session_id": "beat-abc123",
        },
    }
    print(f"\n  Sample trace event:\n    {json.dumps(sample_event, indent=4)}")


def demo_lifecycle() -> None:
    """Show the full lifecycle diagram."""
    print("\n" + "=" * 60)
    print("§05 — LIFECYCLE")
    print("=" * 60)
    print("""
  parent beat (depth 0) — the ONLY spawner
    │  plan → decide to delegate
    ├─ spawn_subagent("researcher", "research competitors")    depth 1
    │    → SubagentResult(output="## Findings\\n...", success=True)
    ├─ spawn_subagent("copy_editor", "polish this draft")      depth 1
    │    → SubagentResult(output="[polished copy]", success=True)
    │  parent folds results into its reasoning
    └─ continues working with subagent outputs

  Key invariants:
    • Subagents are leaves — they CANNOT spawn sub-subagents
    • Spawn cap: max 10 per beat (cheap early guard)
    • Budget: two-gate model applies (gate-2 = cost backstop)
    • Nothing persists: ephemeral → no standing row in chorus
    """)


def main() -> None:
    print("\n" + "=" * 60)
    print("  DREAM SUBAGENT SYSTEM — COMPREHENSIVE DEMO")
    print("  GTM Engineer with Researcher & Copy Editor subagents")
    print("=" * 60)

    # 1. Data model + registry
    researcher, copy_editor, agent_set = demo_data_model()

    # 2. Projection
    demo_projection(agent_set)

    # 3. Spawn tool
    demo_spawn_tool_simulation(agent_set)

    # 4. Observability
    demo_observability()

    # 5. Lifecycle
    demo_lifecycle()

    print("\n" + "=" * 60)
    print("  DEMO COMPLETE — all subagent primitives exercised")
    print("=" * 60)


if __name__ == "__main__":
    main()
