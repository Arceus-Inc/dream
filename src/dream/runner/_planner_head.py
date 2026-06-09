"""Production planner head: spec 10 slice G3.

``make_planner_head`` builds a :data:`PlannerCallable` that drives one
planner-bound session through :meth:`Harness.run_role` and parses the
model's reply into a ``(spec_markdown, ledger)`` pair
:func:`dream.planner.run_planner` can commit to the worktree.

The model is asked for a strict envelope::

    <spec>
    # narrative spec markdown
    </spec>
    <ledger>
    {"steps": [{"id": "...", "description": "..."}], ...}
    </ledger>

The parser is tolerant of an inner ```json fence inside ``<ledger>``
(models love to add one) and of prose around the two tags.

Failures parse into :class:`PlannerHeadParseError` — callers (the runner
or a future retry wrapper) decide whether to escalate or re-prompt;
engine-layer failures surface as :class:`dream.runner.RoleSessionError`
unchanged.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dream.planner import LedgerStep, PlannerLedger, PlannerOutput

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.runner._observer import RunTaskObserver

__all__ = [
    "PLANNER_INSTRUCTION_TEMPLATE",
    "PlannerHeadParseError",
    "make_planner_head",
]


class PlannerHeadParseError(RuntimeError):
    """Raised when the planner's reply does not match the spec/ledger envelope."""


# JSON example is kept as a separate constant so the prompt builder doesn't
# have to double every ``{`` / ``}`` to escape format-string syntax.
_LEDGER_EXAMPLE = """\
{
  "steps": [
    {"id": "<unique-slug>", "description": "<one sentence>",
     "sprint_target": <int|null>, "notes": "<optional>"}
  ],
  "evaluator_enabled": true
}"""


# Exposed so a future test / operator can introspect the contract without
# poking at the formatter.
PLANNER_INSTRUCTION_TEMPLATE = (
    "You are drafting the sprint plan for task {task_id}.\n"
    "\n"
    "USER INTENT\n"
    "-----------\n"
    "{intent}\n"
    "\n"
    "OUTPUT FORMAT\n"
    "-------------\n"
    "Reply with exactly two XML-style sections in this order:\n"
    "\n"
    "<spec>\n"
    "# narrative spec markdown describing the goal, approach, and any\n"
    "# constraints the generator must respect.\n"
    "</spec>\n"
    "<ledger>\n"
    "{example}\n"
    "</ledger>\n"
    "\n"
    "Requirements:\n"
    '- The <spec> body must be non-empty markdown.\n'
    '- The <ledger> body must be valid JSON with at least one step.\n'
    '- Each step needs "id" (string) and "description" (string).\n'
    '- "sprint_target" (int) and "notes" (string) are optional.\n'
    '- Set "evaluator_enabled": false only when verifier signal is\n'
    "  unavailable or actively misleading; default true.\n"
    "\n"
    "DECOMPOSITION\n"
    "-------------\n"
    "- Use the FEWEST steps that cover the intent. Each step is a full\n"
    "  generator+evaluator sprint, so over-splitting wastes sprints and\n"
    "  produces steps the evaluator cannot independently verify.\n"
    "- A single cohesive deliverable is ONE step. For example, a module\n"
    "  plus its unit test plus running the test is one step, not three.\n"
    "- Do NOT add a separate documentation, README, or changelog step\n"
    "  unless the intent explicitly asks for documentation.\n"
    "- Split into multiple steps only for genuinely independent units of\n"
    "  work (distinct features, files, or layers that can land separately).\n"
)


_SPEC_RE = re.compile(r"<spec>\s*(.*?)\s*</spec>", re.DOTALL | re.IGNORECASE)
_LEDGER_RE = re.compile(
    r"<ledger>\s*(.*?)\s*</ledger>", re.DOTALL | re.IGNORECASE
)
_FENCE_RE = re.compile(
    r"^```(?:[A-Za-z0-9_+\-]+)?\s*\n(.*?)\n```\s*$", re.DOTALL
)


def _build_intent(task_id: str, intent: str) -> str:
    return PLANNER_INSTRUCTION_TEMPLATE.format(
        task_id=task_id, intent=intent, example=_LEDGER_EXAMPLE
    )


def _extract_spec(reply: str) -> str:
    match = _SPEC_RE.search(reply)
    if match is None:
        raise PlannerHeadParseError(
            "planner reply missing <spec>...</spec> section"
        )
    body = match.group(1).strip()
    if not body:
        raise PlannerHeadParseError("planner <spec> section is empty")
    return body


def _extract_ledger_json(reply: str) -> dict[str, Any]:
    match = _LEDGER_RE.search(reply)
    if match is None:
        raise PlannerHeadParseError(
            "planner reply missing <ledger>...</ledger> section"
        )
    raw = match.group(1).strip()
    fence = _FENCE_RE.match(raw)
    if fence is not None:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlannerHeadParseError(
            f"planner <ledger> is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise PlannerHeadParseError(
            f"planner <ledger> must be a JSON object, got {type(data).__name__}"
        )
    return data


def _build_steps(raw_steps: Any) -> tuple[LedgerStep, ...]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlannerHeadParseError(
            "planner ledger must contain at least one step"
        )
    out: list[LedgerStep] = []
    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise PlannerHeadParseError(f"step {i} is not a JSON object")
        step_id = raw.get("id")
        if not isinstance(step_id, str) or not step_id:
            raise PlannerHeadParseError(f"step {i} missing 'id' string")
        description = raw.get("description")
        if not isinstance(description, str) or not description:
            raise PlannerHeadParseError(
                f"step {step_id!r} missing 'description' string"
            )
        sprint_target = raw.get("sprint_target")
        if sprint_target is not None and not isinstance(sprint_target, int):
            raise PlannerHeadParseError(
                f"step {step_id!r} 'sprint_target' must be int or null"
            )
        notes = raw.get("notes", "")
        if not isinstance(notes, str):
            raise PlannerHeadParseError(
                f"step {step_id!r} 'notes' must be a string"
            )
        out.append(
            LedgerStep(
                id=step_id,
                description=description,
                sprint_target=sprint_target,
                notes=notes,
            )
        )
    return tuple(out)


def _build_ledger(
    *, task_id: str, intent: str, data: dict[str, Any]
) -> PlannerLedger:
    steps = _build_steps(data.get("steps"))
    evaluator_enabled = data.get("evaluator_enabled", True)
    if not isinstance(evaluator_enabled, bool):
        raise PlannerHeadParseError(
            "ledger 'evaluator_enabled' must be a bool, got "
            f"{type(evaluator_enabled).__name__}"
        )
    return PlannerLedger(
        task_id=task_id,
        intent=intent,
        created_at=time.time(),
        steps=steps,
        evaluator_enabled=evaluator_enabled,
    )


def make_planner_head(
    harness: Harness,
    *,
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
) -> Callable[[str, str], Awaitable[PlannerOutput]]:
    """Build a :data:`PlannerCallable` driven by :meth:`Harness.run_role`.

    The returned coroutine asks the planner LLM for a strict
    ``<spec>...</spec><ledger>...</ledger>`` envelope, parses the reply,
    and yields a :class:`PlannerOutput` ready for
    :func:`dream.planner.run_planner` to commit to the worktree.

    ``harness_dir`` is forwarded to ``run_role`` so per-task role overlays
    in ``{harness_dir}/roles/planner.toml`` are honoured. ``observer``
    is forwarded so :func:`dream.runner.run_task` can stream the
    planner's text and tool calls in real time.
    """

    async def planner(task_id: str, intent: str) -> PlannerOutput:
        prompt = _build_intent(task_id, intent)
        result = await harness.run_role(
            "planner", prompt, harness_dir=harness_dir, observer=observer
        )
        spec = _extract_spec(result.final_text)
        ledger_data = _extract_ledger_json(result.final_text)
        ledger = _build_ledger(
            task_id=task_id, intent=intent, data=ledger_data
        )
        return PlannerOutput(spec_markdown=spec, ledger=ledger)

    return planner
