"""Bounded contract negotiation between generator and evaluator.

Spec 10 acceptance criteria:

- #9: contract negotiation MUST be bounded to ≤ 3 rounds.
- #11: the evaluator MUST NOT renegotiate after rendering judgement —
  this module exercises the *proposal* phase only; the verdict phase
  lives in :mod:`dream.sprint._evaluation`.

When the cap is reached without agreement, the evaluator's final
proposal becomes the contract (``imposed=true``) and a warning event
is emitted so the runner can surface it to the leader.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ._contract import NegotiationEntry, SprintContract

__all__ = [
    "EvaluatorPropose",
    "GeneratorRespond",
    "NegotiationResult",
    "build_contract_from_negotiation",
    "negotiate_contract",
]


EvaluatorPropose = Callable[
    [int, list[NegotiationEntry]],
    "list[str] | Awaitable[list[str]]",
]
"""``(round_num, log_so_far) -> proposed acceptance criteria``.

May return the proposal directly (sync) or as an awaitable (e.g. an LLM-backed
head opening a session via :meth:`Harness.run_role`).
"""

GeneratorRespond = Callable[
    [int, list[NegotiationEntry], list[str]],
    "tuple[bool, list[str] | None] | Awaitable[tuple[bool, list[str] | None]]",
]
"""``(round_num, log_so_far, evaluator_proposal) -> (accept?, counter_or_None)``.

May return synchronously or as an awaitable, like :data:`EvaluatorPropose`.
"""


@dataclass(frozen=True)
class NegotiationResult:
    """Outcome of one negotiation session."""

    criteria: tuple[str, ...]
    log: tuple[NegotiationEntry, ...]
    imposed: bool
    rounds: int
    warning_event: dict[str, Any] | None = field(default=None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _seed_carry_log(carry_items: tuple[str, ...]) -> list[NegotiationEntry]:
    """Surface unresolved items from the prior sprint into this contract's
    log so reviewers can trace why a criterion was raised."""
    return [
        NegotiationEntry(
            ts=_now_iso(),
            from_role="carry",
            to_role="evaluator",
            message=f"carry-over from prior sprint: {item}",
        )
        for item in carry_items
    ]


def negotiate_contract(
    *,
    evaluator_propose: EvaluatorPropose,
    generator_respond: GeneratorRespond,
    max_rounds: int = 3,
    carry_items: tuple[str, ...] = (),
) -> NegotiationResult:
    """Run a bounded back-and-forth and return the final criteria + log.

    Each ``round`` is one evaluator-proposal + one generator-response.
    On acceptance the result uses the generator's accepted proposal
    (the evaluator's current). On cap-out without acceptance, the
    evaluator's final proposal wins (``imposed=True``) and
    ``warning_event`` is populated.

    Sync seam; for an LLM-backed negotiation use
    :func:`negotiate_contract_async`.
    """
    return _run_negotiation(
        evaluator_propose=evaluator_propose,
        generator_respond=generator_respond,
        max_rounds=max_rounds,
        carry_items=carry_items,
        await_results=False,
    )


async def negotiate_contract_async(
    *,
    evaluator_propose: EvaluatorPropose,
    generator_respond: GeneratorRespond,
    max_rounds: int = 3,
    carry_items: tuple[str, ...] = (),
) -> NegotiationResult:
    """Async counterpart that awaits awaitables returned by the callables.

    Accepts both sync- and async-shaped callables; the runner uses this so
    LLM-backed negotiator heads (Spec 10-H) can open ``Harness.run_role``
    sessions per round without a sync-over-async bridge.
    """
    gen = _run_negotiation_async(
        evaluator_propose=evaluator_propose,
        generator_respond=generator_respond,
        max_rounds=max_rounds,
        carry_items=carry_items,
    )
    return await gen


def _run_negotiation(
    *,
    evaluator_propose: EvaluatorPropose,
    generator_respond: GeneratorRespond,
    max_rounds: int,
    carry_items: tuple[str, ...],
    await_results: bool,
) -> NegotiationResult:
    """Shared sync body used by :func:`negotiate_contract`.

    ``await_results`` is always ``False`` here; the async path lives in
    :func:`_run_negotiation_async` to keep the await keyword out of the
    sync function body.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")

    log: list[NegotiationEntry] = list(_seed_carry_log(carry_items))
    last_proposal: list[str] = []
    rounds = 0

    for round_num in range(1, max_rounds + 1):
        rounds = round_num
        raw = evaluator_propose(round_num, list(log))
        if inspect.isawaitable(raw):
            raise TypeError(
                "evaluator_propose returned an awaitable; use "
                "negotiate_contract_async for LLM-backed heads"
            )
        proposal = list(raw)
        last_proposal = proposal
        log.append(
            NegotiationEntry(
                ts=_now_iso(),
                from_role="evaluator",
                to_role="generator",
                message=f"propose r{round_num}: {proposal}",
            )
        )
        raw_resp = generator_respond(round_num, list(log), list(proposal))
        if inspect.isawaitable(raw_resp):
            raise TypeError(
                "generator_respond returned an awaitable; use "
                "negotiate_contract_async for LLM-backed heads"
            )
        accept, counter = raw_resp
        log.append(
            NegotiationEntry(
                ts=_now_iso(),
                from_role="generator",
                to_role="evaluator",
                message=(
                    f"accept r{round_num}"
                    if accept
                    else f"counter r{round_num}: {counter or []}"
                ),
            )
        )
        if accept:
            return NegotiationResult(
                criteria=tuple(proposal),
                log=tuple(log),
                imposed=False,
                rounds=rounds,
                warning_event=None,
            )

    return _imposed_result(last_proposal, log, rounds)


async def _run_negotiation_async(
    *,
    evaluator_propose: EvaluatorPropose,
    generator_respond: GeneratorRespond,
    max_rounds: int,
    carry_items: tuple[str, ...],
) -> NegotiationResult:
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")

    log: list[NegotiationEntry] = list(_seed_carry_log(carry_items))
    last_proposal: list[str] = []
    rounds = 0

    for round_num in range(1, max_rounds + 1):
        rounds = round_num
        raw = evaluator_propose(round_num, list(log))
        if inspect.isawaitable(raw):
            raw = await raw
        proposal = list(raw)
        last_proposal = proposal
        log.append(
            NegotiationEntry(
                ts=_now_iso(),
                from_role="evaluator",
                to_role="generator",
                message=f"propose r{round_num}: {proposal}",
            )
        )
        raw_resp = generator_respond(round_num, list(log), list(proposal))
        if inspect.isawaitable(raw_resp):
            raw_resp = await raw_resp
        accept, counter = raw_resp
        log.append(
            NegotiationEntry(
                ts=_now_iso(),
                from_role="generator",
                to_role="evaluator",
                message=(
                    f"accept r{round_num}"
                    if accept
                    else f"counter r{round_num}: {counter or []}"
                ),
            )
        )
        if accept:
            return NegotiationResult(
                criteria=tuple(proposal),
                log=tuple(log),
                imposed=False,
                rounds=rounds,
                warning_event=None,
            )

    return _imposed_result(last_proposal, log, rounds)


def _imposed_result(
    last_proposal: list[str], log: list[NegotiationEntry], rounds: int
) -> NegotiationResult:
    warning = {
        "type": "sprint.negotiation_imposed",
        "level": "warning",
        "rounds": rounds,
        "imposed_criteria": list(last_proposal),
    }
    return NegotiationResult(
        criteria=tuple(last_proposal),
        log=tuple(log),
        imposed=True,
        rounds=rounds,
        warning_event=warning,
    )


def build_contract_from_negotiation(
    result: NegotiationResult,
    *,
    task_id: str,
    sprint_number: int,
    goal: str,
    verification_steps: tuple[dict[str, str], ...],
    evaluator_enabled: bool = True,
    scope_includes: tuple[str, ...] = (),
    scope_excludes: tuple[str, ...] = (),
) -> SprintContract:
    """Assemble a :class:`SprintContract` from a negotiation outcome."""
    return SprintContract(
        task_id=task_id,
        sprint_number=sprint_number,
        goal=goal,
        scope_includes=scope_includes,
        scope_excludes=scope_excludes,
        acceptance_criteria=result.criteria,
        verification_steps=verification_steps,
        evaluator_enabled=evaluator_enabled,
        imposed=result.imposed,
        negotiation_log=result.log,
    )
