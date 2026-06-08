"""Tests for sprint-contract negotiation between generator and evaluator.

Spec 10 acceptance criteria #9, #11 + "Negotiation hits the cap and the
evaluator's proposal is imposed" gherkin scenario:

- MUST be bounded to ≤ 3 rounds (#9).
- MUST NOT negotiate after rendering judgement (#11) — the evaluator's
  proposal-time and verdict-time roles are disjoint; this module exercises
  the proposal side only.
- When the cap is hit without agreement, the committed contract uses the
  evaluator's last proposal, ``imposed=true``, and a warning event is
  recorded.
"""

from __future__ import annotations

import pytest


# --- helpers -----------------------------------------------------------


def _eval_always(criteria: tuple[str, ...]):
    """Build an evaluator that proposes ``criteria`` every round."""

    def _f(round_num: int, log):
        return list(criteria)

    return _f


def _gen_accept_after(accept_round: int, counter: tuple[str, ...] = ()):
    """Build a generator that accepts on a given round, countering before."""

    def _f(round_num: int, log, evaluator_proposal):
        if round_num >= accept_round:
            return (True, None)
        return (False, list(counter))

    return _f


# --- acceptance --------------------------------------------------------


def test_negotiate_returns_evaluator_proposal_when_generator_accepts_round_one() -> None:
    from dream.sprint import negotiate_contract

    result = negotiate_contract(
        evaluator_propose=_eval_always(("MUST x", "SHOULD y")),
        generator_respond=_gen_accept_after(1),
    )
    assert result.criteria == ("MUST x", "SHOULD y")
    assert result.imposed is False
    assert result.rounds == 1
    assert result.warning_event is None


def test_negotiate_log_records_each_exchange() -> None:
    from dream.sprint import negotiate_contract

    result = negotiate_contract(
        evaluator_propose=_eval_always(("MUST x",)),
        generator_respond=_gen_accept_after(2, counter=("MUST y",)),
    )
    assert len(result.log) >= 3  # eval propose, gen counter, eval propose, gen accept
    roles_seen = {(e.from_role, e.to_role) for e in result.log}
    assert ("evaluator", "generator") in roles_seen
    assert ("generator", "evaluator") in roles_seen


# --- bounded at 3 rounds ----------------------------------------------


def test_negotiate_bounded_at_three_rounds() -> None:
    from dream.sprint import negotiate_contract

    gen_calls = []

    def gen_never_accepts(round_num, log, evaluator_proposal):
        gen_calls.append(round_num)
        return (False, ["MUST z"])

    eval_calls = []

    def eval_count(round_num, log):
        eval_calls.append(round_num)
        return ["MUST x"]

    result = negotiate_contract(
        evaluator_propose=eval_count,
        generator_respond=gen_never_accepts,
        max_rounds=3,
    )
    assert result.rounds == 3
    assert eval_calls == [1, 2, 3]
    assert len(gen_calls) == 3


def test_negotiation_cap_imposes_evaluator_proposal() -> None:
    from dream.sprint import negotiate_contract

    def evaluator(round_num, log):
        # final round proposal is distinguishable
        return [f"MUST round-{round_num}"]

    def gen_never_accepts(round_num, log, evaluator_proposal):
        return (False, ["MUST gen-counter"])

    result = negotiate_contract(
        evaluator_propose=evaluator,
        generator_respond=gen_never_accepts,
        max_rounds=3,
    )
    assert result.imposed is True
    assert result.criteria == ("MUST round-3",)


def test_negotiation_cap_emits_warning_event() -> None:
    from dream.sprint import negotiate_contract

    result = negotiate_contract(
        evaluator_propose=lambda r, log: ["MUST x"],
        generator_respond=lambda r, log, p: (False, ["MUST y"]),
        max_rounds=3,
    )
    assert result.warning_event is not None
    assert result.warning_event["type"] == "sprint.negotiation_imposed"
    assert result.warning_event["level"] == "warning"
    assert result.warning_event["rounds"] == 3


def test_negotiate_rejects_max_rounds_less_than_one() -> None:
    from dream.sprint import negotiate_contract

    with pytest.raises(ValueError, match="max_rounds"):
        negotiate_contract(
            evaluator_propose=lambda r, log: ["x"],
            generator_respond=lambda r, log, p: (True, None),
            max_rounds=0,
        )


# --- carry items from prior eval --------------------------------------


def test_negotiate_seeds_log_with_carry_items() -> None:
    """needs-changes outcomes carry their items into the next contract's
    negotiation_log (spec 10 §Outcome rules)."""
    from dream.sprint import negotiate_contract

    result = negotiate_contract(
        evaluator_propose=_eval_always(("MUST x",)),
        generator_respond=_gen_accept_after(1),
        carry_items=("redo step 3 with foo", "verify bar"),
    )
    carry_msgs = [e.message for e in result.log if "carry" in e.from_role]
    assert any("redo step 3 with foo" in m for m in carry_msgs)
    assert any("verify bar" in m for m in carry_msgs)


# --- contract assembly -------------------------------------------------


def test_build_contract_from_negotiation_result() -> None:
    from dream.sprint import SprintContract, build_contract_from_negotiation, negotiate_contract

    result = negotiate_contract(
        evaluator_propose=_eval_always(("MUST x",)),
        generator_respond=_gen_accept_after(1),
    )
    contract = build_contract_from_negotiation(
        result,
        task_id="t1",
        sprint_number=1,
        goal="ship slice",
        verification_steps=({"kind": "test", "ref": "tests/test_foo.py"},),
        evaluator_enabled=True,
    )
    assert isinstance(contract, SprintContract)
    assert contract.acceptance_criteria == ("MUST x",)
    assert contract.imposed is False
    assert contract.negotiation_log == result.log
