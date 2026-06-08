"""Production generator head: spec 10 slice G4.

``make_generator_head`` builds a :data:`GeneratorExecute` that drives one
generator-bound session through :meth:`Harness.run_role`. Unlike the
planner head, the generator's output is **the worktree itself** — tool
calls write files, run tests, and commit. There is nothing to parse;
the head's job is to assemble a complete prompt from the sprint
contract + step and forward it.

When ``contract`` is ``None`` (evaluator disabled — see spec 10
§"Disabling the evaluator"), the head emits a stripped-down prompt
that names only the step and warns the model that no automated
verifier will second-guess its work.

Engine-layer failures surface as :class:`dream.runner.RoleSessionError`
unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.sprint import SprintContract

__all__ = ["make_generator_head"]


def _format_step(step: LedgerStep) -> str:
    lines = [
        "STEP",
        "----",
        f"{step.id}: {step.description}",
    ]
    if step.notes.strip():
        lines += ["", "NOTES", "-----", step.notes.strip()]
    return "\n".join(lines)


def _format_contract(contract: SprintContract) -> str:
    parts: list[str] = []

    parts += ["GOAL", "----", contract.goal]

    parts += ["", "ACCEPTANCE CRITERIA  (all must hold when you're done)", "-" * 50]
    parts += [f"- {ac}" for ac in contract.acceptance_criteria]

    if contract.verification_steps:
        parts += [
            "",
            "VERIFICATION STEPS  (run these before declaring done)",
            "-" * 50,
        ]
        for vs in contract.verification_steps:
            kind = vs.get("kind", "?")
            command = vs.get("command", "")
            parts.append(f"- [{kind}] {command}")

    if contract.scope_includes:
        parts += ["", "SCOPE INCLUDES  (touch only these paths)", "-" * 40]
        parts += [f"- {p}" for p in contract.scope_includes]

    if contract.scope_excludes:
        parts += ["", "SCOPE EXCLUDES  (do not touch)", "-" * 32]
        parts += [f"- {p}" for p in contract.scope_excludes]

    return "\n".join(parts)


def _build_intent(
    *,
    task_id: str,
    sprint_number: int,
    contract: SprintContract | None,
    step: LedgerStep,
) -> str:
    header = (
        f"You are executing sprint {sprint_number} of task {task_id}.\n"
    )

    if contract is None:
        # Evaluator disabled — no contract on disk, no second pair of eyes.
        body = (
            f"{header}\n"
            "The evaluator is disabled for this task; there is no automated\n"
            "verifier to catch mistakes. Self-check thoroughly before stopping.\n"
            "\n"
            f"{_format_step(step)}\n"
            "\n"
            "Make the smallest change that achieves the step's goal.\n"
        )
        return body

    return (
        f"{header}\n"
        f"{_format_contract(contract)}\n"
        "\n"
        f"{_format_step(step)}\n"
        "\n"
        "Make the smallest change that satisfies every acceptance criterion.\n"
        "Run the verification steps and confirm they pass before declaring\n"
        "the step complete.\n"
    )


def make_generator_head(
    harness: Harness,
    *,
    harness_dir: Path | None = None,
) -> Callable[
    [str, int, SprintContract | None, LedgerStep],
    Awaitable[None],
]:
    """Build a :data:`GeneratorExecute` driven by :meth:`Harness.run_role`.

    The returned coroutine opens a ``generator``-bound session per call
    and lets the model perform the work via its allowed tools. It
    returns ``None`` on completion; :class:`RoleSessionError` propagates
    on a mid-stream engine error.

    ``harness_dir`` is forwarded so per-task role overlays in
    ``{harness_dir}/roles/generator.toml`` are honoured.
    """

    async def generator(
        task_id: str,
        sprint_number: int,
        contract: SprintContract | None,
        step: LedgerStep,
    ) -> None:
        prompt = _build_intent(
            task_id=task_id,
            sprint_number=sprint_number,
            contract=contract,
            step=step,
        )
        await harness.run_role(
            "generator", prompt, harness_dir=harness_dir
        )

    return generator
