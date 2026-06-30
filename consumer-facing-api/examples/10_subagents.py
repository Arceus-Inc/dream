"""10 — subagents: capability-minimized teammates dispatched mid-beat.

`build_harness(subagents=...)` wires a `SubagentSet` — declared subagent
templates the parent agent dispatches via the `spawn_subagent` tool. Each
subagent runs as a **real bounded session** with its own tool access (read_file,
grep, bash, etc.), turn budget, and capability-minimized permissions. It returns
plain text and dissolves. Nothing persists.

Two tiers:

- **Tier-1 (role-owned):** domain specialists declared on a role — e.g. an
  engineer's code reviewer.
- **Tier-2 (shared registry):** role-agnostic capability agents any beat
  can pull in — e.g. a researcher, a copy editor.

Capability minimization (narrower-wins): a subagent's tools and permissions
are *always* a subset of its parent. ``spawn_subagent`` is always disallowed
to prevent recursive spawning (v1 flat depth).

Observability: the `spawn_subagent` tool emits `subagent.spawn` and
`subagent.complete` trace events into the OTel-shaped JSONL trace, so the
full subagent lifecycle appears alongside llm.call / tool.call events.
The evaluator can find these via ``query_logs(all_sessions=True)``.

Run:  DREAM_MODEL=... DREAM_API_KEY=... DREAM_BASE_URL=... \\
      uv run python consumer-facing-api/examples/10_subagents.py
"""

from __future__ import annotations

import asyncio
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver
from dream.subagents import Subagent, SubagentRegistry, SubagentSet
from dream.subagents._projection import build_subagent_set


def _build_subagents() -> SubagentSet:
    """Wire a simple subagent set: one role-owned reviewer + one shared researcher."""

    # Tier-1: role-owned — a code reviewer that can read and inspect code
    reviewer = Subagent(
        name="reviewer",
        description=(
            "Reviews code changes for correctness, style, and edge cases. "
            "Reads the actual files, runs grep to find patterns, and returns "
            "a structured review with findings and suggestions."
        ),
        tools=("read_file", "grep", "bash", "git"),
        depth=1,
        max_turns=6,
    )

    # Tier-2: shared capability agent — a researcher that explores the codebase
    researcher = Subagent(
        name="researcher",
        description=(
            "Researches a topic by reading files, searching the codebase, "
            "and reasoning about the findings. Returns structured insights."
        ),
        tools=("read_file", "grep", "bash"),
        depth=1,
        max_turns=4,
    )

    # Register Tier-2 agent in the shared registry
    registry = SubagentRegistry()
    registry.register(researcher)

    # Build the resolved set (both tiers merged)
    return build_subagent_set(
        tier1_agents=[reviewer],
        tier2_agents=registry.resolve(("researcher",)),
    )


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()
    subagent_set = _build_subagents()

    print(f"workspace: {workspace}")
    print(f"subagents: {subagent_set.names()}")
    reviewer = subagent_set.get("reviewer")
    researcher = subagent_set.get("researcher")
    if reviewer:
        print(f"  reviewer  — {reviewer.description[:60]}...")
    if researcher:
        print(f"  researcher — {researcher.description[:60]}...")
    print()

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        subagents=subagent_set,
    )

    async with harness:
        result = await harness.run_task(
            intent=(
                "Create a Python module `calculator.py` with add, subtract, "
                "multiply, divide functions, and `test_calculator.py` with "
                "pytest tests for each. Before writing the tests, use the "
                "spawn_subagent tool to dispatch the 'researcher' subagent "
                "with the prompt 'Research best practices for writing pytest "
                "test suites for math utilities — parameterized tests, edge "
                "cases, division by zero handling'. Then after writing the "
                "code, use spawn_subagent to dispatch the 'reviewer' subagent "
                "to review your implementation. Run pytest to confirm tests pass."
            ),
            observer=StdioObserver(sys.stdout),
            max_sprints=4,
        )

    print(f"\ntask {result.task_id} finished in {len(result.sprints)} sprint(s)")
    for step in result.final_ledger.steps:
        print(f"  step {step.id}: {step.status}")


if __name__ == "__main__":
    asyncio.run(main())
