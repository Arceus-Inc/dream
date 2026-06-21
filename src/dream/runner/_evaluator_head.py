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
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dream.runner._head_retry import ask_until_parsed
from dream.runner._oracle import OracleResult, run_oracle
from dream.sprint import EvaluationRecord

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.runner._observer import RunTaskObserver
    from dream.sprint import SprintContract

__all__ = [
    "EVALUATOR_INSTRUCTION_TEMPLATE",
    "EvaluatorHeadParseError",
    "make_evaluator_head",
]


# v2: the oracle block (spec 15 P3) changed the prompt and added the
# oracle-red → needs-changes downgrade; v1 verdicts judged without evidence.
DEFAULT_EVALUATOR_VERSION = "head-v2"


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
    "Read the changed files and decide whether every acceptance criterion holds.\n"
    "You are read-only and have no shell of your own: any verification commands are\n"
    "run for you by the harness, and their results appear in the ORACLE section\n"
    "below (absent that section, no commands were configured). Judge each criterion\n"
    "from the code and artefacts you can read plus any oracle evidence. Do NOT\n"
    "withhold a 'pass' merely because you could not personally run a command — if the\n"
    "code satisfies the criteria and no oracle failure is shown, return 'pass'.\n"
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

    if contract.rubric:
        parts += [
            "",
            "REVIEW RUBRIC  (judge the artefact against this; it must hold for a 'pass')",
            "-" * 60,
            contract.rubric,
        ]

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
    *,
    task_id: str,
    sprint_number: int,
    contract: SprintContract,
    step: LedgerStep,
    oracle: OracleResult | None = None,
) -> str:
    contract_block = _format_contract_block(contract)
    if oracle is not None:
        contract_block = f"{contract_block}\n\n{oracle.render_block()}"
    return EVALUATOR_INSTRUCTION_TEMPLATE.format(
        task_id=task_id,
        sprint_number=sprint_number,
        contract_block=contract_block,
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
    observer: RunTaskObserver | None = None,
    worktree_root: Path | None = None,
    oracle_timeout_seconds: float = 300.0,
) -> Callable[
    [str, int, SprintContract, LedgerStep],
    Awaitable[EvaluationRecord],
]:
    """Build an :data:`EvaluatorRun` driven by :meth:`Harness.run_role`.

    The returned coroutine first executes the contract's verification
    steps as real subprocesses (the oracle, spec 15 P3 — run in
    ``worktree_root``, defaulting to the harness working dir), injects
    the structured results into the verdict prompt, then asks the
    evaluator LLM for a strict ``<verdict>{JSON}</verdict>`` envelope.
    The evaluator judges *evidence*: when verification steps exist, a
    model ``pass`` over a red oracle is downgraded to ``needs-changes``
    with the failing commands as carry items.

    ``harness_dir`` is forwarded so per-task role overlays in
    ``{harness_dir}/roles/evaluator.toml`` are honoured.

    ``evaluator_version`` is stamped onto every record this head
    produces; bump it when the prompt or parser changes in a way that
    invalidates prior verdicts.
    """
    oracle_cwd = (
        worktree_root if worktree_root is not None else harness.config.working_dir
    )

    async def evaluator(
        task_id: str,
        sprint_number: int,
        contract: SprintContract,
        step: LedgerStep,
    ) -> EvaluationRecord:
        oracle = await run_oracle(
            contract, cwd=oracle_cwd, timeout_seconds=oracle_timeout_seconds
        )
        prompt = _build_intent(
            task_id=task_id,
            sprint_number=sprint_number,
            contract=contract,
            step=step,
            oracle=oracle,
        )
        async def _ask(p: str) -> Any:
            return await harness.run_role(
                "evaluator", p, harness_dir=harness_dir, observer=observer
            )

        def _on_retry(attempt: int, err: Exception) -> None:
            if observer is not None:
                observer.on_event(
                    {
                        "kind": "head.retry",
                        "role": "evaluator",
                        "attempt": attempt,
                        "error": str(err),
                    }
                )

        def _parse(final_text: str) -> EvaluationRecord:
            data = _extract_verdict_json(final_text)
            return _coerce_record(
                task_id=task_id,
                sprint_number=sprint_number,
                step_id=step.id,
                evaluator_version=evaluator_version,
                data=data,
            )

        record = await ask_until_parsed(
            _ask,
            _parse,
            prompt=prompt,
            parse_error=EvaluatorHeadParseError,
            on_retry=_on_retry,
        )
        return _enforce_oracle(record, oracle)

    return evaluator


def _enforce_oracle(
    record: EvaluationRecord, oracle: OracleResult | None
) -> EvaluationRecord:
    """The hard gate: a red oracle invalidates a model ``pass``.

    On any non-green oracle the failing commands ride along as carry
    items so the generator sees the concrete failures next sprint.
    """
    if oracle is None or oracle.green:
        return record
    items = record.items + oracle.failure_items()
    if record.outcome != "pass":
        return replace(record, items=items)
    note = "oracle override: model said pass but executed verification steps failed"
    notes = f"{record.notes} [{note}]" if record.notes else note
    return replace(record, outcome="needs-changes", items=items, notes=notes)
