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

The parser prefers the ``<verdict>`` section when present but treats the
wrapper as **optional** — the typed JSON object is the contract, so a bare,
```json-fenced, or prose-embedded JSON verdict is accepted too. Only a reply
with no parseable JSON object at all parses into
:class:`EvaluatorHeadParseError`; engine-layer failures surface as
:class:`dream.runner.RoleSessionError` unchanged.

Unlike the generator head, ``contract`` is always present here —
``run_task`` skips the evaluator entirely when it is disabled, so this
head never sees ``None``.

Verification runs **inside** the evaluator session via ``bash`` (Hermes /
Claude Code shape). There is no harness ``run_oracle`` sidecar.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dream.runner._head_retry import ask_until_parsed
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


# v3: in-session bash verify; oracle sidecar removed (Hermes/CC-aligned).
DEFAULT_EVALUATOR_VERSION = "head-v3"


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
            "VERIFICATION STEPS  (run these yourself via bash against the generator's output)",
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


def _extract_verdict_json(reply: str) -> dict[str, Any]:
    """Extract the typed JSON verdict from the reply.

    The verdict *object* is the typed contract; the ``<verdict>...</verdict>`` wrapper is optional. We
    prefer the tagged section when present (back-compat + disambiguation), but fall back to the JSON
    object anywhere in the reply — bare, ```json-fenced, or embedded in prose — so a model that emits
    clean typed JSON without the XML wrapper is accepted rather than hard-failing. Only a reply with no
    parseable JSON object at all is a parse error.
    """
    match = _VERDICT_RE.search(reply)
    raw = match.group(1).strip() if match is not None else reply.strip()
    data = _loads_json_object(raw)
    if data is None:
        raise EvaluatorHeadParseError(
            "evaluator reply did not contain a JSON verdict object"
        )
    return data


def _loads_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of ``text`` — fence-aware, with a brace-slice fallback. ``None`` if none."""
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
            data = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


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
    task_intent: str = "",
    harness_dir: Path | None = None,
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
    observer: RunTaskObserver | None = None,
    worktree_root: Path | None = None,  # kept for call-site compat; unused (no oracle)
    oracle_timeout_seconds: float = 300.0,  # kept for call-site compat; unused
) -> Callable[
    [str, int, SprintContract, LedgerStep],
    Awaitable[EvaluationRecord],
]:
    """Build an :data:`EvaluatorRun` driven by :meth:`Harness.run_role`.

    The evaluator LLM session has ``bash`` and runs verification itself.
    There is no pre-session ``run_oracle`` subprocess.

    ``task_intent`` is the original task Intent (source of truth). When set,
    it is embedded so pass cannot rest on a weaker substitute than the Intent.

    ``harness_dir`` is forwarded so per-task role overlays in
    ``{harness_dir}/roles/evaluator.toml`` are honoured.

    ``evaluator_version`` is stamped onto every record this head
    produces; bump it when the prompt or parser changes in a way that
    invalidates prior verdicts.

    ``worktree_root`` / ``oracle_timeout_seconds`` are accepted but ignored
    (ponytail: keep call sites green; remove in a later cleanup).
    """
    del worktree_root, oracle_timeout_seconds

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

        return await ask_until_parsed(
            _ask,
            _parse,
            prompt=prompt,
            parse_error=EvaluatorHeadParseError,
            on_retry=_on_retry,
        )

    return evaluator
