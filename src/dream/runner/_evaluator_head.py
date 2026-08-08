"""Production evaluator head: spec 10 slice G5 + structured-output P2.

``make_evaluator_head`` builds an :data:`EvaluatorRun` that drives one
evaluator-bound session through :meth:`Harness.run_role` with a native
``response_format`` JSON schema and parses the model's verdict into an
:class:`EvaluationRecord`.

The typed contract is :class:`EvaluatorVerdict` (JSON object). The optional
``<verdict>...</verdict>`` wrapper remains accepted for parse tolerance, but
the prompt asks for bare JSON under constrained decode.

Verification runs **inside** the evaluator session via ``bash``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from dream.api.response_format import ResponseFormat
from dream.runner._evaluator_schema import EVALUATOR_VERDICT_SCHEMA, EvaluatorVerdict
from dream.runner._head_retry import ask_until_parsed
from dream.runner._role_session import role_session_id
from dream.session import SessionOptions
from dream.sprint import EvaluationRecord

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.runner._observer import RunTaskObserver
    from dream.runner._role_session import RunRoleResult
    from dream.sprint import SprintContract

__all__ = [
    "EVALUATOR_INSTRUCTION_TEMPLATE",
    "EvaluatorHeadParseError",
    "make_evaluator_head",
]


DEFAULT_EVALUATOR_VERSION = "head-v4"


class EvaluatorHeadParseError(RuntimeError):
    """Raised when the evaluator's reply does not match the verdict contract."""


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
    "{intent_block}"
    "{contract_block}\n"
    "\n"
    "STEP UNDER REVIEW\n"
    "-----------------\n"
    "{step_id}: {step_description}\n"
    "\n"
    "WHAT TO DO\n"
    "----------\n"
    "Read the changed files. You have bash: run verification yourself in this session.\n"
    "- If VERIFICATION STEPS are listed above: run each command via bash.\n"
    "- If none: discover this repo's test/build gate from manifests/lockfiles and run it\n"
    "  (stack-agnostic — do not assume pytest or any one stack).\n"
    "Judge every acceptance criterion and the REVIEW RUBRIC (if present) from the\n"
    "artefacts you read AND the tool output you just produced. outcome=pass only when\n"
    "those gates exited 0 (or the rubric honestly allows absence for report-only work).\n"
    "Never invent green results. Do not modify source files.\n"
    "\n"
    "INTENT FIDELITY\n"
    "---------------\n"
    "TASK INTENT is the source of truth. Pass requires the deliverable to meet the\n"
    "Intent as stated — not a weaker or narrower substitute that is easier to mark\n"
    "done. Verification that only covers a reduced contract is still needs-changes\n"
    "(or fail if there is no honest repair path).\n"
    "\n"
    "OUTCOME SEMANTICS (durable ledger — choose carefully)\n"
    "----------------------------------------------------\n"
    "- pass: every acceptance criterion and the rubric hold; verification exited 0;\n"
    "  and the work matches TASK INTENT (no weakened substitute).\n"
    "- needs-changes: verification is red OR criteria/Intent fidelity incomplete, AND\n"
    "  you can list concrete items the generator can fix in-tree on the next sprint.\n"
    "  Prefer needs-changes whenever useful repair items exist — that keeps the step\n"
    "  in_progress so repair can continue.\n"
    "- fail: no honest repair path (abandoned / impossible / wrong problem / unsafe\n"
    "  to continue). fail durable-blocks the step; do not use it for ordinary red\n"
    "  verification or missing criteria you can still describe as items.\n"
    "\n"
    "OUTPUT FORMAT\n"
    "-------------\n"
    "After tools finish, reply with ONE JSON object (no XML, no prose, no fences):\n"
    "\n"
    "{example}\n"
    "\n"
    "Requirements:\n"
    '- "outcome" must be one of: pass, needs-changes, fail.\n'
    '- "score" (0..1 float) is optional; defaults to 0.0.\n'
    '- "notes" (string) is optional.\n'
    '- "items" (list of strings) lists follow-ups the generator must address\n'
    "  on the next sprint; required when outcome is needs-changes.\n"
)


_VERDICT_RE = re.compile(r"<verdict>\s*(.*?)\s*</verdict>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:[A-Za-z0-9_+\-]+)?\s*\n(.*?)\n```\s*$", re.DOTALL)


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
            "VERIFICATION STEPS  (run these yourself via bash against the generator's output)",
            "-" * 60,
        ]
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


def _format_task_intent_block(task_intent: str) -> str:
    """Render TASK INTENT for the evaluator prompt (empty when unset)."""
    text = task_intent.strip()
    if not text:
        return ""
    return (
        "TASK INTENT  (source of truth — do not accept a weaker substitute)\n"
        "------------------------------------------------------------------\n"
        f"{text}\n"
        "\n"
    )


def _build_intent(
    *,
    task_id: str,
    sprint_number: int,
    contract: SprintContract,
    step: LedgerStep,
    task_intent: str = "",
) -> str:
    return EVALUATOR_INSTRUCTION_TEMPLATE.format(
        task_id=task_id,
        sprint_number=sprint_number,
        intent_block=_format_task_intent_block(task_intent),
        contract_block=_format_contract_block(contract),
        step_id=step.id,
        step_description=step.description,
        example=_VERDICT_EXAMPLE,
    )


def _extract_verdict_json_text(reply: str) -> str:
    """Extract a JSON object text from the reply (tagged, fenced, bare, or embedded)."""
    match = _VERDICT_RE.search(reply)
    raw = match.group(1).strip() if match is not None else reply.strip()
    text = _unwrap_json_object_text(raw)
    if text is None:
        raise EvaluatorHeadParseError("evaluator reply did not contain a JSON verdict object")
    return text


def _unwrap_json_object_text(text: str) -> str | None:
    candidate = text.strip()
    fence = _FENCE_RE.match(candidate)
    if fence is not None:
        candidate = fence.group(1).strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    sliced = candidate[start : end + 1] if start != -1 and end > start else ""
    for attempt in (candidate, sliced):
        if not attempt:
            continue
        try:
            loaded: object = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return attempt
    return None


def parse_evaluator_verdict(
    reply: str,
    *,
    task_id: str,
    sprint_number: int,
    step_id: str,
    evaluator_version: str,
) -> EvaluationRecord:
    """Parse evaluator final text into an :class:`EvaluationRecord`."""
    text = _extract_verdict_json_text(reply)
    try:
        verdict = EvaluatorVerdict.model_validate_json(text)
    except ValidationError as exc:
        raise EvaluatorHeadParseError(f"evaluator verdict failed schema validation: {exc}") from exc

    return EvaluationRecord(
        task_id=task_id,
        sprint_number=sprint_number,
        step_id=step_id,
        outcome=verdict.outcome.value,
        score=verdict.score,
        notes=verdict.notes,
        items=tuple(verdict.items),
        evaluator_version=evaluator_version,
    )


def make_evaluator_head(
    harness: Harness,
    *,
    task_intent: str = "",
    harness_dir: Path | None = None,
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
    observer: RunTaskObserver | None = None,
    worktree_root: Path | None = None,
    oracle_timeout_seconds: float = 300.0,
    session_scope: str | None = None,
) -> Callable[
    [str, int, SprintContract, LedgerStep],
    Awaitable[EvaluationRecord],
]:
    """Build an :data:`EvaluatorRun` driven by :meth:`Harness.run_role`.

    The evaluator LLM session has ``bash`` and runs verification itself.
    Final replies are constrained by :data:`EVALUATOR_VERDICT_SCHEMA`.

    ``session_scope`` names the task's evaluator thread, so successive sprints
    are judged by an evaluator that remembers what it already accepted.
    """
    del worktree_root, oracle_timeout_seconds
    session_id = (
        None if session_scope is None else role_session_id(session_scope, "evaluator")
    )
    response_format = ResponseFormat.for_schema(
        EVALUATOR_VERDICT_SCHEMA,
        name="evaluator_verdict",
        strict=True,
    )

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
            task_intent=task_intent,
        )

        async def _ask(p: str) -> RunRoleResult:
            return await harness.run_role(
                "evaluator",
                p,
                harness_dir=harness_dir,
                observer=observer,
                options=SessionOptions(response_format=response_format),
                session_id=session_id,
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
            return parse_evaluator_verdict(
                final_text,
                task_id=task_id,
                sprint_number=sprint_number,
                step_id=step.id,
                evaluator_version=evaluator_version,
            )

        return await ask_until_parsed(
            _ask,
            _parse,
            prompt=prompt,
            parse_error=EvaluatorHeadParseError,
            on_retry=_on_retry,
        )

    return evaluator
