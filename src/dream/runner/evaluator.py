"""Production evaluator head + verdict schema (structured-output P2).

``make_evaluator_head`` builds an :data:`EvaluatorRun` that drives one
evaluator-bound session through :meth:`Harness.run_role` with a native
``response_format`` JSON schema and parses the model's verdict into an
:class:`EvaluationRecord`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dream.api.response_format import JsonSchema, ResponseFormat
from dream.runner.envelopes import ask_until_parsed, format_sprint_beat
from dream.runner.events import HeadRetry, RunTaskObserver
from dream.runner.role import RunRoleResult, role_session_id
from dream.session import SessionOptions
from dream.sprint import EvaluationRecord

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.sprint import SprintContract

__all__ = [
    "EVALUATOR_INSTRUCTION_TEMPLATE",
    "EVALUATOR_USER_ENVELOPE_TEMPLATE",
    "EVALUATOR_VERDICT_SCHEMA",
    "EvaluatorHeadParseError",
    "EvaluatorVerdict",
    "VerdictOutcome",
    "make_evaluator_head",
    "parse_evaluator_verdict",
]


DEFAULT_EVALUATOR_VERSION = "head-v4"


class VerdictOutcome(StrEnum):
    """Durable evaluator outcomes (matches :data:`dream.sprint.EvaluationOutcome`)."""

    PASS = "pass"
    NEEDS_CHANGES = "needs-changes"
    FAIL = "fail"


class EvaluatorVerdict(BaseModel):
    """JSON object the evaluator head must emit."""

    model_config = ConfigDict(extra="forbid")

    outcome: VerdictOutcome
    score: float = Field(ge=0.0, le=1.0)
    notes: str
    items: list[str]


EVALUATOR_VERDICT_SCHEMA: JsonSchema = JsonSchema.of(EvaluatorVerdict.model_json_schema())


class EvaluatorHeadParseError(RuntimeError):
    """Raised when the evaluator's reply does not match the verdict contract."""


_VERDICT_EXAMPLE = """\
{
  "outcome": "pass",
  "score": 0.0,
  "notes": "<optional one-liner summary>",
  "items": ["<follow-up item>", "..."]
}"""


EVALUATOR_USER_ENVELOPE_TEMPLATE = (
    "{beat}\n"
    "OUTPUT SCHEMA (reply with ONE JSON object after tools; no fences):\n"
    "\n"
    "{example}\n"
)

# Back-compat alias for older imports.
EVALUATOR_INSTRUCTION_TEMPLATE = EVALUATOR_USER_ENVELOPE_TEMPLATE


_VERDICT_RE = re.compile(r"<verdict>\s*(.*?)\s*</verdict>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:[A-Za-z0-9_+\-]+)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _build_user_envelope(
    *,
    task_id: str,
    sprint_number: int,
    contract: SprintContract,
    step: LedgerStep,
    task_intent: str = "",
) -> str:
    beat = format_sprint_beat(
        task_id=task_id,
        sprint_number=sprint_number,
        contract=contract,
        step=step,
        task_intent=task_intent,
        audience="evaluator",
    )
    return EVALUATOR_USER_ENVELOPE_TEMPLATE.format(beat=beat, example=_VERDICT_EXAMPLE)


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
    session_scope: str | None = None,
) -> Callable[
    [str, int, SprintContract, LedgerStep],
    Awaitable[EvaluationRecord],
]:
    """Build an :data:`EvaluatorRun` driven by :meth:`Harness.run_role`."""
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
        prompt = _build_user_envelope(
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
                    HeadRetry(role="evaluator", attempt=attempt, error=str(err))
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
            session_reuse=session_id is not None,
        )

    return evaluator
