"""Production planner head: spec 10 slice G3 + structured-output P2.

``make_planner_head`` builds a :data:`PlannerCallable` that drives one
planner-bound session through :meth:`Harness.run_role` with a native
``response_format`` JSON schema, then parses the reply into a
``(spec_markdown, ledger)`` pair :func:`dream.planner.run_planner` can commit.

The model emits one JSON object matching :class:`PlannerResponse`::

    {
      "spec_markdown": "# narrative ...",
      "ledger": {
        "steps": [
          {"id": "...", "description": "...", "acceptance_criteria": ["..."]}
        ],
        "evaluator_enabled": true
      }
    }

Each step names its own acceptance criteria: the sprint contract is built
straight from them, so nothing is negotiated once planning is done.

:func:`ask_until_parsed` remains the outer retry when local validation fails.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from dream.api.response_format import ResponseFormat
from dream.planner import LedgerStep, PlannerLedger, PlannerOutput
from dream.runner._head_retry import ask_until_parsed
from dream.runner._planner_schema import PLANNER_RESPONSE_SCHEMA, PlannerResponse
from dream.runner._role_session import role_session_id
from dream.session import SessionOptions

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.runner._observer import RunTaskObserver
    from dream.runner._role_session import RunRoleResult

__all__ = [
    "PLANNER_USER_ENVELOPE_TEMPLATE",
    "PlannerHeadParseError",
    "make_planner_head",
]


class PlannerHeadParseError(RuntimeError):
    """Raised when the planner's reply does not match the JSON contract."""


# JSON example is kept as a separate constant so the prompt builder doesn't
# have to double every ``{`` / ``}`` to escape format-string syntax.
_LEDGER_EXAMPLE = """\
{
  "spec_markdown": "# narrative spec markdown describing the goal, approach, and constraints",
  "ledger": {
    "steps": [
      {"id": "<unique-slug>", "description": "<one sentence>",
       "acceptance_criteria": ["<checkable statement>", "..."],
       "sprint_target": null, "notes": ""}
    ],
    "evaluator_enabled": true
  }
}"""


PLANNER_USER_ENVELOPE_TEMPLATE = (
    "Sprint plan request for task {task_id}.\n"
    "\n"
    "USER INTENT\n"
    "-----------\n"
    "{intent}\n"
    "\n"
    "OUTPUT SCHEMA (reply with ONE JSON object; no fences):\n"
    "\n"
    "{example}\n"
)

# Back-compat alias for older imports / tests.
PLANNER_INSTRUCTION_TEMPLATE = PLANNER_USER_ENVELOPE_TEMPLATE


def _build_user_envelope(task_id: str, intent: str) -> str:
    return PLANNER_USER_ENVELOPE_TEMPLATE.format(
        task_id=task_id, intent=intent, example=_LEDGER_EXAMPLE
    )


def parse_planner_response(reply: str, *, task_id: str, intent: str) -> PlannerOutput:
    """Parse planner final text into :class:`PlannerOutput`.

    Accepts bare JSON or a single fenced JSON block. Raises
    :class:`PlannerHeadParseError` on schema/validation failure.
    """
    text = _strip_optional_fence(reply.strip())
    try:
        payload = PlannerResponse.model_validate_json(text)
    except ValidationError as exc:
        raise PlannerHeadParseError(f"planner reply failed schema validation: {exc}") from exc
    except ValueError as exc:
        raise PlannerHeadParseError(f"planner reply is not valid JSON: {exc}") from exc

    steps = tuple(
        LedgerStep(
            id=step.id,
            description=step.description,
            acceptance_criteria=tuple(step.acceptance_criteria),
            sprint_target=step.sprint_target,
            notes=step.notes,
        )
        for step in payload.ledger.steps
    )
    ledger = PlannerLedger(
        task_id=task_id,
        intent=intent,
        created_at=time.time(),
        steps=steps,
        evaluator_enabled=payload.ledger.evaluator_enabled,
    )
    return PlannerOutput(spec_markdown=payload.spec_markdown, ledger=ledger)


def _strip_optional_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def make_planner_head(
    harness: Harness,
    *,
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
    session_scope: str | None = None,
) -> Callable[[str, str], Awaitable[PlannerOutput]]:
    """Build a :data:`PlannerCallable` driven by :meth:`Harness.run_role`.

    The returned coroutine asks the planner LLM for a schema-constrained JSON
    object, parses the reply, and yields a :class:`PlannerOutput` ready for
    :func:`dream.planner.run_planner` to commit to the worktree.

    ``session_scope`` names the planner's resumable thread within the task, so
    a later call continues the conversation instead of starting over. Parse
    retries share that thread, which lets the model see its own rejected reply.
    """
    session_id = None if session_scope is None else role_session_id(session_scope, "planner")
    response_format = ResponseFormat.for_schema(
        PLANNER_RESPONSE_SCHEMA,
        name="planner_response",
        strict=True,
    )

    async def planner(task_id: str, intent: str) -> PlannerOutput:
        prompt = _build_user_envelope(task_id, intent)

        async def _ask(p: str) -> RunRoleResult:
            return await harness.run_role(
                "planner",
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
                        "role": "planner",
                        "attempt": attempt,
                        "error": str(err),
                    }
                )

        def _parse_for_task(final_text: str) -> PlannerOutput:
            return parse_planner_response(final_text, task_id=task_id, intent=intent)

        return await ask_until_parsed(
            _ask,
            _parse_for_task,
            prompt=prompt,
            parse_error=PlannerHeadParseError,
            on_retry=_on_retry,
            session_reuse=session_id is not None,
        )

    return planner
