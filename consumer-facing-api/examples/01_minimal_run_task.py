"""01 — the smallest possible end-to-end task.

One build_harness call, one run_task call. The planner writes a spec + step
ledger, the sprint loop executes and evaluates, and the result tells you what
happened to every step.

Run:  uv run python consumer-facing-api/examples/01_minimal_run_task.py
"""

from __future__ import annotations

import asyncio
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()
    print(f"workspace: {workspace}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
    )
    async with harness:
        result = await harness.run_task(
            intent=(
                "Create greet.py exposing greet(name) returning 'Hi ' + name, "
                "and test_greet.py asserting greet('Sam') == 'Hi Sam'. "
                "Run pytest to confirm the test passes."
            ),
            observer=StdioObserver(sys.stdout),
            max_sprints=4,
        )

    print(f"\ntask {result.task_id} finished in {len(result.sprints)} sprint(s)")
    for step in result.final_ledger.steps:
        print(f"  step {step.id}: {step.status}")
    print(f"spec:   {result.spec_path}")
    print(f"ledger: {result.ledger_path}")


if __name__ == "__main__":
    asyncio.run(main())
