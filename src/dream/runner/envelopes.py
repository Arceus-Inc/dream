"""Sprint beat envelopes, generator head, and self-healing head retry.

``format_sprint_beat`` renders data-only user turns for generator /
evaluator. ``make_generator_head`` forwards that beat into ``run_role``.
``ask_until_parsed`` re-prompts parse-strict heads on malformed JSON.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar

if TYPE_CHECKING:
    from dream.engine._messages import ConversationMessage
    from dream.harness import Harness
    from dream.planner import LedgerStep
    from dream.runner.events import RunTaskObserver
    from dream.sprint import SprintContract

Audience = Literal["generator", "evaluator"]

__all__ = [
    "DEFAULT_RETRIES",
    "ask_until_parsed",
    "format_sprint_beat",
    "make_generator_head",
]

# Default retry budget: 2 retries = 3 total attempts.
DEFAULT_RETRIES: int = 2

T = TypeVar("T")


def _format_task_intent(task_intent: str) -> str:
    text = task_intent.strip()
    if not text:
        return ""
    return f"TASK INTENT\n-----------\n{text}\n\n"


def _format_step(step: LedgerStep) -> str:
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
        step_block = _format_step(step)
    else:
        header = f"Verify sprint {sprint_number} of task {task_id}.\n"
        step_block = (
            "STEP UNDER REVIEW\n"
            "-----------------\n"
            f"{step.id}: {step.description}"
        )

    intent_block = _format_task_intent(task_intent)
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


def make_generator_head(
    harness: Harness,
    *,
    task_intent: str = "",
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
    session_scope: str | None = None,
    resume_messages: Sequence[ConversationMessage] | None = None,
) -> Callable[
    [str, int, SprintContract | None, LedgerStep],
    Awaitable[None],
]:
    """Build a :data:`GeneratorExecute` driven by :meth:`Harness.run_role`.

    ``task_intent`` is embedded as a data block so the original Intent stays
    visible beside the sprint contract. Phase protocol lives in standing orders.
    ``resume_messages`` seeds the first generator session; each sprint/retry
    then rebinds to that run's transcript so later beats keep prior history
    instead of replaying the original list.
    """
    from dream.runner.role import role_session_id

    session_id = (
        None if session_scope is None else role_session_id(session_scope, "generator")
    )
    prior_messages: list[ConversationMessage] = list(resume_messages or ())

    async def generator(
        task_id: str,
        sprint_number: int,
        contract: SprintContract | None,
        step: LedgerStep,
    ) -> None:
        nonlocal prior_messages
        prompt = format_sprint_beat(
            task_id=task_id,
            sprint_number=sprint_number,
            contract=contract,
            step=step,
            task_intent=task_intent,
            audience="generator",
        )
        result = await harness.run_role(
            "generator",
            prompt,
            harness_dir=harness_dir,
            observer=observer,
            session_id=session_id,
            resume_messages=prior_messages,
        )
        prior_messages = list(result.messages)

    return generator


class _HasFinalText(Protocol):
    """Minimal interface the helper requires from an ask() result."""

    @property
    def final_text(self) -> str: ...


async def ask_until_parsed(
    ask: Callable[[str], Awaitable[_HasFinalText]],
    parse: Callable[[str], T],
    *,
    prompt: str,
    parse_error: type[Exception],
    retries: int = DEFAULT_RETRIES,
    on_retry: Callable[[int, Exception], None] | None = None,
    session_reuse: bool = False,
) -> T:
    """Ask the model and parse, retrying with feedback on parse failures.

    When ``session_reuse`` is true, retries send a short correction (the
    shared session transcript already contains the original prompt and the
    rejected reply). Otherwise the full original prompt is re-sent with the
    error and previous reply quoted.
    """
    current_prompt = prompt
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        result = await ask(current_prompt)
        final_text: str = result.final_text

        try:
            return parse(final_text)
        except parse_error as exc:  # type: ignore[misc,unused-ignore]
            last_exc = exc
            if attempt >= retries:
                raise
            attempt_number = attempt + 1
            if on_retry is not None:
                on_retry(attempt_number, exc)
            current_prompt = _build_feedback_prompt(
                original_prompt=prompt,
                error=exc,
                previous_text=final_text,
                session_reuse=session_reuse,
            )

    assert last_exc is not None
    raise last_exc  # pragma: no cover


def _build_feedback_prompt(
    *,
    original_prompt: str,
    error: Exception,
    previous_text: str,
    session_reuse: bool,
) -> str:
    """Build a retry prompt that asks for JSON matching the response schema."""
    correction = (
        f"Your previous reply could not be used: {error}\n"
        "Re-emit your COMPLETE reply as JSON matching the response schema, "
        "and nothing else."
    )
    if session_reuse:
        return correction
    return (
        f"{original_prompt}"
        "\n\n"
        f"{correction}\n"
        "Your previous reply is below between <previous-reply> tags.\n"
        "<previous-reply>\n"
        f"{previous_text}\n"
        "</previous-reply>"
    )
