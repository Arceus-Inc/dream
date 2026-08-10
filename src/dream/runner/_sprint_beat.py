"""Shared data-only sprint beat envelopes for generator and evaluator user turns.

Standing orders own phase protocol; this module only renders headers + values
from the live contract / step / intent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dream.planner import LedgerStep
    from dream.sprint import SprintContract

Audience = Literal["generator", "evaluator"]

__all__ = ["format_sprint_beat", "format_step", "format_task_intent"]


def format_task_intent(task_intent: str) -> str:
    """Render the TASK INTENT data block (empty when unset)."""
    text = task_intent.strip()
    if not text:
        return ""
    return f"TASK INTENT\n-----------\n{text}\n\n"


def format_step(step: LedgerStep) -> str:
    """Render STEP / NOTES data blocks."""
    lines = [
        "STEP",
        "----",
        f"{step.id}: {step.description}",
    ]
    if step.notes.strip():
        lines += ["", "NOTES", "-----", step.notes.strip()]
    return "\n".join(lines)


def format_sprint_beat(
    *,
    task_id: str,
    sprint_number: int,
    contract: SprintContract | None,
    step: LedgerStep,
    task_intent: str = "",
    audience: Audience,
) -> str:
    """Build a data-only user envelope for a generator or evaluator beat."""
    if audience == "generator":
        header = f"Execute sprint {sprint_number} of task {task_id}.\n"
        step_block = format_step(step)
    else:
        header = f"Verify sprint {sprint_number} of task {task_id}.\n"
        step_block = (
            "STEP UNDER REVIEW\n"
            "-----------------\n"
            f"{step.id}: {step.description}"
        )

    intent_block = format_task_intent(task_intent)
    if contract is None:
        return (
            f"{header}\n"
            f"{intent_block}"
            "Evaluator disabled for this task.\n"
            "\n"
            f"{step_block}\n"
        )

    return (
        f"{header}\n"
        f"{intent_block}"
        f"{_format_contract(contract)}\n"
        "\n"
        f"{step_block}\n"
    )


def _format_contract(contract: SprintContract) -> str:
    parts: list[str] = ["GOAL", "----", contract.goal]

    parts += ["", "ACCEPTANCE CRITERIA", "-" * 19]
    parts += [f"- {ac}" for ac in contract.acceptance_criteria]

    if contract.rubric:
        parts += ["", "REVIEW RUBRIC", "-" * 13, contract.rubric]

    if contract.verification_steps:
        parts += ["", "VERIFICATION STEPS", "-" * 18]
        for vs in contract.verification_steps:
            kind = str(vs.get("kind", "?"))
            command = str(vs.get("command", ""))
            parts.append(f"- [{kind}] {command}")

    if contract.scope_includes:
        parts += ["", "SCOPE INCLUDES", "-" * 14]
        parts += [f"- {p}" for p in contract.scope_includes]

    if contract.scope_excludes:
        parts += ["", "SCOPE EXCLUDES", "-" * 14]
        parts += [f"- {p}" for p in contract.scope_excludes]

    return "\n".join(parts)
