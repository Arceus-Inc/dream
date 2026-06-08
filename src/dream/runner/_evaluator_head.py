"""Production evaluator head: spec 10 slice G5.

``make_evaluator_head`` builds an :data:`EvaluatorRun` that drives one
evaluator-bound session through :meth:`Harness.run_role` and parses
the model's verdict into an :class:`EvaluationRecord` the runner can
persist via :func:`dream.sprint.record_evaluation`.

The model is asked for a strict envelope::

    <verdict>
    {"outcome": "pass"|"needs-changes"|"fail",
     "score": 0.0,
     "notes": "...",
     "items": ["..."]}
    </verdict>

The parser tolerates an inner ```json fence inside ``<verdict>`` and
prose around the tag. Failures parse into
:class:`EvaluatorHeadParseError`; engine-layer failures surface as
:class:`dream.runner.RoleSessionError` unchanged.

Unlike the generator head, ``contract`` is always present here —
``run_task`` skips the evaluator entirely when it is disabled, so this
head never sees ``None``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dream.sprint import EvaluationRecord

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.sprint import SprintContract

__all__ = [
    "EVALUATOR_INSTRUCTION_TEMPLATE",
    "EvaluatorHeadParseError",
    "make_evaluator_head",
]


DEFAULT_EVALUATOR_VERSION = "head-v1"


class EvaluatorHeadParseError(RuntimeError):
    """Raised when the evaluator's reply does not match the verdict envelope."""


_VALID_OUTCOMES = frozenset({"pass", "needs-changes", "fail"})


_VERDICT_EXAMPLE = """\
{
  "outcome": "pass",
  "score": 0.0,
  "notes": "<optional one-liner summary>",
  "items": ["<follow-up item>", "..."]
}"""


EVALUATOR_INSTRUCTION_TEMPLATE = (
    "You are verifying sprint {sprint_number} of task {task_id}.\n"
    "\n"
    "{contract_block}\n"
    "\n"
    "STEP UNDER REVIEW\n"
    "-----------------\n"
    "{step_id}: {step_description}\n"
    "\n"
    "WHAT TO DO\n"
    "----------\n"
    "Read the changed files, run the contract's verification steps, and\n"
    "decide whether every acceptance criterion holds.\n"
    "\n"
    "OUTPUT FORMAT\n"
    "-------------\n"
    "Reply with exactly one XML-style section:\n"
    "\n"
    "<verdict>\n"
    "{example}\n"
    "</verdict>\n"
    "\n"
    "Requirements:\n"
    '- "outcome" must be one of: pass, needs-changes, fail.\n'
    '- "score" (0..1 float) is optional; defaults to 0.0.\n'
    '- "notes" (string) is optional.\n'
    '- "items" (list of strings) lists follow-ups the generator must address\n'
    "  on the next sprint; required when outcome is needs-changes.\n"
)


_VERDICT_RE = re.compile(
    r"<verdict>\s*(.*?)\s*</verdict>", re.DOTALL | re.IGNORECASE
)
_FENCE_RE = re.compile(
    r"^```(?:[A-Za-z0-9_+\-]+)?\s*\n(.*?)\n```\s*$", re.DOTALL
)


def _format_contract_block(contract: SprintContract) -> str:
    parts: list[str] = ["GOAL", "----", contract.goal]

    parts += [
        "",
        "ACCEPTANCE CRITERIA  (every one must hold for a 'pass' outcome)",
        "-" * 60,
    ]
    parts += [f"- {ac}" for ac in contract.acceptance_criteria]

    if contract.verification_steps:
        parts += [
            "",
            "VERIFICATION STEPS  (run these against the generator's output)",
            "-" * 60,
        ]
        for vs in contract.verification_steps:
            kind = vs.get("kind", "?")
            command = vs.get("command", "")
            parts.append(f"- [{kind}] {command}")

    if contract.scope_includes:
        parts += ["", "SCOPE INCLUDES", "-" * 14]
        parts += [f"- {p}" for p in contract.scope_includes]

    if contract.scope_excludes:
        parts += ["", "SCOPE EXCLUDES", "-" * 14]
        parts += [f"- {p}" for p in contract.scope_excludes]

    return "\n".join(parts)


def _build_intent(
    *, task_id: str, sprint_number: int, contract: SprintContract, step: LedgerStep
) -> str:
    return EVALUATOR_INSTRUCTION_TEMPLATE.format(
        task_id=task_id,
        sprint_number=sprint_number,
        contract_block=_format_contract_block(contract),
        step_id=step.id,
        step_description=step.description,
        example=_VERDICT_EXAMPLE,
    )


def _extract_verdict_json(reply: str) -> dict[str, Any]:
    match = _VERDICT_RE.search(reply)
    if match is None:
        raise EvaluatorHeadParseError(
            "evaluator reply missing <verdict>...</verdict> section"
        )
    raw = match.group(1).strip()
    fence = _FENCE_RE.match(raw)
    if fence is not None:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvaluatorHeadParseError(
            f"evaluator <verdict> is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise EvaluatorHeadParseError(
            f"evaluator <verdict> must be a JSON object, got {type(data).__name__}"
        )
    return data


def _coerce_record(
    *,
    task_id: str,
    sprint_number: int,
    step_id: str,
    evaluator_version: str,
    data: dict[str, Any],
) -> EvaluationRecord:
    outcome = data.get("outcome")
    if not isinstance(outcome, str) or outcome not in _VALID_OUTCOMES:
        raise EvaluatorHeadParseError(
            f"evaluator outcome missing or invalid: {outcome!r}; "
            f"expected one of {sorted(_VALID_OUTCOMES)}"
        )

    score_raw = data.get("score", 0.0)
    if isinstance(score_raw, bool) or not isinstance(score_raw, (int, float)):
        raise EvaluatorHeadParseError(
            f"evaluator 'score' must be a number, got {type(score_raw).__name__}"
        )
    score = float(score_raw)

    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise EvaluatorHeadParseError(
            f"evaluator 'notes' must be a string, got {type(notes).__name__}"
        )

    items_raw = data.get("items", ())
    if not isinstance(items_raw, (list, tuple)):
        raise EvaluatorHeadParseError(
            f"evaluator 'items' must be a list, got {type(items_raw).__name__}"
        )
    items: list[str] = []
    for i, item in enumerate(items_raw):
        if not isinstance(item, str):
            raise EvaluatorHeadParseError(
                f"evaluator item {i} must be a string, got {type(item).__name__}"
            )
        items.append(item)

    return EvaluationRecord(
        task_id=task_id,
        sprint_number=sprint_number,
        step_id=step_id,
        outcome=outcome,  # type: ignore[arg-type]
        score=score,
        notes=notes,
        items=tuple(items),
        evaluator_version=evaluator_version,
    )


def make_evaluator_head(
    harness: Harness,
    *,
    harness_dir: Path | None = None,
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
) -> Callable[
    [str, int, SprintContract, LedgerStep],
    Awaitable[EvaluationRecord],
]:
    """Build an :data:`EvaluatorRun` driven by :meth:`Harness.run_role`.

    The returned coroutine asks the evaluator LLM for a strict
    ``<verdict>{JSON}</verdict>`` envelope, parses the reply, and yields
    an :class:`EvaluationRecord` ready for
    :func:`dream.sprint.record_evaluation` to commit.

    ``harness_dir`` is forwarded so per-task role overlays in
    ``{harness_dir}/roles/evaluator.toml`` are honoured.

    ``evaluator_version`` is stamped onto every record this head
    produces; bump it when the prompt or parser changes in a way that
    invalidates prior verdicts.
    """

    async def evaluator(
        task_id: str,
        sprint_number: int,
        contract: SprintContract,
        step: LedgerStep,
    ) -> EvaluationRecord:
        prompt = _build_intent(
            task_id=task_id,
            sprint_number=sprint_number,
            contract=contract,
            step=step,
        )
        result = await harness.run_role(
            "evaluator", prompt, harness_dir=harness_dir
        )
        data = _extract_verdict_json(result.final_text)
        return _coerce_record(
            task_id=task_id,
            sprint_number=sprint_number,
            step_id=step.id,
            evaluator_version=evaluator_version,
            data=data,
        )

    return evaluator
