"""07 — custom heads: swap one LLM head, keep the rest stock.

Every head of the loop (planner / generator / evaluator-propose /
generator-respond / evaluator-run) is a plain async callable you can replace.
Here a deterministic evaluator auto-passes any sprint that produced the
expected file — useful when you have a programmatic oracle and don't want to
spend model calls on judging. The planner and generator stay live.

Run:  uv run python consumer-facing-api/examples/07_custom_heads.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver
from dream.sprint._evaluation import EvaluationRecord


def make_oracle_evaluator(workspace: Path, expected: str):
    """Evaluator-run head: pass iff the expected artifact exists."""

    async def evaluator_run(
        task_id: str, sprint_number: int, contract: Any, step: Any
    ) -> EvaluationRecord:
        produced = (workspace / expected).exists()
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_number,
            step_id=step.id,
            outcome="pass" if produced else "needs-changes",
            score=1.0 if produced else 0.0,
            notes="" if produced else f"{expected} was not created — create exactly that file",
        )

    return evaluator_run


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
            intent="Create a file named report.txt containing the word 'done'.",
            evaluator_run=make_oracle_evaluator(workspace, "report.txt"),
            observer=StdioObserver(sys.stdout),
            max_sprints=4,
        )

    print(f"\nsprints used: {len(result.sprints)}")
    for sprint in result.sprints:
        print(f"  sprint {sprint.sprint_number}: step={sprint.step_id} outcome={sprint.outcome}")


if __name__ == "__main__":
    asyncio.run(main())
