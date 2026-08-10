"""Production generator head: spec 10 slice G4.

``make_generator_head`` builds a :data:`GeneratorExecute` that drives one
generator-bound session through :meth:`Harness.run_role`. The generator's
output is **the worktree itself** — tool calls write files, run tests, and
commit. Protocol lives in standing orders; this head only builds a data
envelope from the sprint contract + step and forwards it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from dream.runner._role_session import role_session_id
from dream.runner._sprint_beat import format_sprint_beat

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.runner._observer import RunTaskObserver
    from dream.sprint import SprintContract

__all__ = ["make_generator_head"]


def make_generator_head(
    harness: Harness,
    *,
    task_intent: str = "",
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
    session_scope: str | None = None,
) -> Callable[
    [str, int, SprintContract | None, LedgerStep],
    Awaitable[None],
]:
    """Build a :data:`GeneratorExecute` driven by :meth:`Harness.run_role`.

    ``task_intent`` is embedded as a data block so the original Intent stays
    visible beside the sprint contract. Phase protocol (fidelity, rubric,
    evaluator-disabled self-check) lives in packaged standing orders.
    """
    session_id = (
        None if session_scope is None else role_session_id(session_scope, "generator")
    )

    async def generator(
        task_id: str,
        sprint_number: int,
        contract: SprintContract | None,
        step: LedgerStep,
    ) -> None:
        prompt = format_sprint_beat(
            task_id=task_id,
            sprint_number=sprint_number,
            contract=contract,
            step=step,
            task_intent=task_intent,
            audience="generator",
        )
        await harness.run_role(
            "generator",
            prompt,
            harness_dir=harness_dir,
            observer=observer,
            session_id=session_id,
        )

    return generator
