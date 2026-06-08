"""Spec 10-H — LLM-backed negotiator heads.

Two factories build callables compatible with the negotiation seam in
:mod:`dream.sprint._negotiation`:

- :func:`make_evaluator_propose_head` returns a
  :data:`~dream.sprint.EvaluatorPropose`-shaped callable that asks the
  evaluator role to propose acceptance criteria in a strict
  ``<proposal>{JSON list}</proposal>`` envelope.

- :func:`make_generator_respond_head` returns a
  :data:`~dream.sprint.GeneratorRespond`-shaped callable that asks the
  generator role to accept or counter the evaluator's proposal in a
  strict ``<response>{"accept": bool, "counter": [...]|null}</response>``
  envelope.

Both heads are *async* — they return an awaitable, so the runner must
drive them through :func:`dream.sprint.negotiate_contract_async`.

Parse failures surface as :class:`EvaluatorProposeHeadParseError` /
:class:`GeneratorRespondHeadParseError` (both ``RuntimeError`` subclasses)
so the runner can distinguish them from
:class:`dream.runner.RoleSessionError` (engine-layer failures).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.runner._observer import RunTaskObserver
    from dream.sprint import NegotiationEntry

__all__ = [
    "EVALUATOR_PROPOSE_INSTRUCTION_TEMPLATE",
    "GENERATOR_RESPOND_INSTRUCTION_TEMPLATE",
    "EvaluatorProposeHeadParseError",
    "GeneratorRespondHeadParseError",
    "make_evaluator_propose_head",
    "make_generator_respond_head",
]


class EvaluatorProposeHeadParseError(RuntimeError):
    """Raised when the evaluator's reply does not match the proposal envelope."""


class GeneratorRespondHeadParseError(RuntimeError):
    """Raised when the generator's reply does not match the response envelope."""


_PROPOSAL_RE = re.compile(
    r"<proposal>\s*(.*?)\s*</proposal>", re.DOTALL | re.IGNORECASE
)
_RESPONSE_RE = re.compile(
    r"<response>\s*(.*?)\s*</response>", re.DOTALL | re.IGNORECASE
)
_FENCE_RE = re.compile(
    r"^```(?:[A-Za-z0-9_+\-]+)?\s*\n(.*?)\n```\s*$", re.DOTALL
)


_PROPOSAL_EXAMPLE = '["<criterion>", "..."]'
_RESPONSE_EXAMPLE = '{"accept": true, "counter": null}'


EVALUATOR_PROPOSE_INSTRUCTION_TEMPLATE = (
    "You are the EVALUATOR opening contract negotiation round {round_num}.\n"
    "\n"
    "Propose the list of acceptance criteria this sprint must meet.\n"
    "Each criterion is a short imperative string the generator can verify.\n"
    "\n"
    "NEGOTIATION LOG\n"
    "---------------\n"
    "{log_block}\n"
    "\n"
    "OUTPUT FORMAT\n"
    "-------------\n"
    "Reply with exactly one XML-style section:\n"
    "\n"
    "<proposal>\n"
    "{example}\n"
    "</proposal>\n"
    "\n"
    "Requirements:\n"
    "- The payload MUST be a JSON list of strings.\n"
    "- Use SHORT, verifiable, imperative criteria (\"MUST ...\", \"SHOULD ...\").\n"
    "- Take the prior log into account: respond to counters, drop items the\n"
    "  generator already accepted, etc.\n"
)


GENERATOR_RESPOND_INSTRUCTION_TEMPLATE = (
    "You are the GENERATOR responding in contract negotiation round {round_num}.\n"
    "\n"
    "The evaluator just proposed the following acceptance criteria:\n"
    "\n"
    "{proposal_block}\n"
    "\n"
    "Decide whether you ACCEPT them as-is or want to COUNTER with a\n"
    "different list. Accepting closes the negotiation; countering will\n"
    "trigger another evaluator round (up to the bounded cap).\n"
    "\n"
    "NEGOTIATION LOG\n"
    "---------------\n"
    "{log_block}\n"
    "\n"
    "OUTPUT FORMAT\n"
    "-------------\n"
    "Reply with exactly one XML-style section:\n"
    "\n"
    "<response>\n"
    "{example}\n"
    "</response>\n"
    "\n"
    "Requirements:\n"
    '- "accept" MUST be a boolean.\n'
    '- "counter" is a JSON list of strings when "accept" is false,\n'
    "  or null/omitted when accepting.\n"
    "- Counter only on substantive disagreement — bounce-back wastes a round.\n"
)


def _format_log(log: list[NegotiationEntry]) -> str:
    if not log:
        return "(empty — this is the opening round)"
    return "\n".join(
        f"- [{entry.ts}] {entry.from_role} → {entry.to_role}: {entry.message}"
        for entry in log
    )


def _format_proposal(proposal: list[str]) -> str:
    if not proposal:
        return "(empty proposal)"
    return "\n".join(f"- {c}" for c in proposal)


def _extract_payload(
    reply: str, *, regex: re.Pattern[str], tag: str, exc_cls: type[RuntimeError]
) -> Any:
    match = regex.search(reply)
    if match is None:
        raise exc_cls(f"reply missing <{tag}>...</{tag}> section")
    raw = match.group(1).strip()
    fence = _FENCE_RE.match(raw)
    if fence is not None:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise exc_cls(f"<{tag}> payload is not valid JSON: {exc.msg}") from exc


def _coerce_proposal(data: Any) -> list[str]:
    if not isinstance(data, list):
        raise EvaluatorProposeHeadParseError(
            f"<proposal> payload must be a JSON list, got {type(data).__name__}"
        )
    out: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, str):
            raise EvaluatorProposeHeadParseError(
                f"<proposal> item {i} must be a string, got {type(item).__name__}"
            )
        out.append(item)
    return out


def _coerce_response(data: Any) -> tuple[bool, list[str] | None]:
    if not isinstance(data, dict):
        raise GeneratorRespondHeadParseError(
            f"<response> payload must be a JSON object, got {type(data).__name__}"
        )
    accept_raw = data.get("accept")
    if not isinstance(accept_raw, bool):
        raise GeneratorRespondHeadParseError(
            f"<response> 'accept' must be a boolean, got {type(accept_raw).__name__}"
        )
    if accept_raw:
        # Accepting implies no counter; ignore whatever was provided.
        return True, None

    counter_raw = data.get("counter")
    if counter_raw is None:
        return False, None
    if not isinstance(counter_raw, list):
        raise GeneratorRespondHeadParseError(
            f"<response> 'counter' must be a list or null, got {type(counter_raw).__name__}"
        )
    counter: list[str] = []
    for i, item in enumerate(counter_raw):
        if not isinstance(item, str):
            raise GeneratorRespondHeadParseError(
                f"<response> counter item {i} must be a string, got {type(item).__name__}"
            )
        counter.append(item)
    return False, counter


def make_evaluator_propose_head(
    harness: Harness,
    *,
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
) -> Callable[[int, list[NegotiationEntry]], Awaitable[list[str]]]:
    """Build an async :data:`~dream.sprint.EvaluatorPropose` over a harness.

    Each call opens an evaluator-bound session via
    :meth:`Harness.run_role`, embeds the running negotiation log in the
    prompt, parses the model's strict ``<proposal>``-envelope reply, and
    returns the proposed criteria as a list of strings.
    """

    async def propose(
        round_num: int, log: list[NegotiationEntry]
    ) -> list[str]:
        prompt = EVALUATOR_PROPOSE_INSTRUCTION_TEMPLATE.format(
            round_num=round_num,
            log_block=_format_log(log),
            example=_PROPOSAL_EXAMPLE,
        )
        result = await harness.run_role(
            "evaluator", prompt, harness_dir=harness_dir, observer=observer
        )
        data = _extract_payload(
            result.final_text,
            regex=_PROPOSAL_RE,
            tag="proposal",
            exc_cls=EvaluatorProposeHeadParseError,
        )
        return _coerce_proposal(data)

    return propose


def make_generator_respond_head(
    harness: Harness,
    *,
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
) -> Callable[
    [int, list[NegotiationEntry], list[str]],
    Awaitable[tuple[bool, list[str] | None]],
]:
    """Build an async :data:`~dream.sprint.GeneratorRespond` over a harness.

    Each call opens a generator-bound session via
    :meth:`Harness.run_role`, embeds the evaluator's proposal plus the
    running negotiation log in the prompt, parses the model's strict
    ``<response>``-envelope reply, and returns ``(accept, counter)``.
    """

    async def respond(
        round_num: int,
        log: list[NegotiationEntry],
        proposal: list[str],
    ) -> tuple[bool, list[str] | None]:
        prompt = GENERATOR_RESPOND_INSTRUCTION_TEMPLATE.format(
            round_num=round_num,
            proposal_block=_format_proposal(proposal),
            log_block=_format_log(log),
            example=_RESPONSE_EXAMPLE,
        )
        result = await harness.run_role(
            "generator", prompt, harness_dir=harness_dir, observer=observer
        )
        data = _extract_payload(
            result.final_text,
            regex=_RESPONSE_RE,
            tag="response",
            exc_cls=GeneratorRespondHeadParseError,
        )
        return _coerce_response(data)

    return respond
