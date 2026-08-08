"""Acceptance criteria come from the plan, not from a negotiation.

The planner already decides what a step *is*; deciding what "done" means for
that step is the same judgement, so it names the criteria at plan time and the
sprint reads them straight off the ledger. That removes the propose/respond
exchange — two role sessions per sprint, six at the cap — from the path before
any code is written.

What this pins:

- A ledger step carries its acceptance criteria and round-trips them on disk.
- The contract for a sprint is assembled from the step.
- A needs-changes evaluation still steers the retry: its unresolved items are
  folded into the criteria of the next contract for that step.
- A step whose criteria the planner left empty still yields a valid contract,
  falling back to the step description rather than failing the sprint.
"""

from __future__ import annotations

from pathlib import Path

from dream.planner import LedgerStep
from dream.sprint import SprintContract, build_contract_from_step


def _step(**overrides: object) -> LedgerStep:
    base: dict[str, object] = {
        "id": "s1",
        "description": "add the retry decorator",
        "acceptance_criteria": ("retries three times", "logs each attempt"),
    }
    base.update(overrides)
    return LedgerStep(**base)  # type: ignore[arg-type]


def test_step_round_trips_acceptance_criteria() -> None:
    restored = LedgerStep.from_dict(_step().to_dict())

    assert restored.acceptance_criteria == ("retries three times", "logs each attempt")


def test_step_without_criteria_omits_the_key() -> None:
    assert "acceptance_criteria" not in _step(acceptance_criteria=()).to_dict()


def test_contract_takes_its_criteria_from_the_step() -> None:
    contract = build_contract_from_step(
        _step(),
        task_id="t-1",
        sprint_number=1,
        goal="ship the retry",
        verification_steps=({"kind": "test", "command": "pytest"},),
    )

    assert contract.acceptance_criteria == ("retries three times", "logs each attempt")
    assert contract.task_id == "t-1"
    assert contract.goal == "ship the retry"


def test_carry_items_are_folded_into_the_retry_contract() -> None:
    contract = build_contract_from_step(
        _step(),
        task_id="t-1",
        sprint_number=2,
        goal="fix the retry",
        verification_steps=(),
        carry_items=("backoff is not exponential",),
    )

    # The evaluator's unresolved item becomes a criterion the retry must meet.
    assert contract.acceptance_criteria == (
        "retries three times",
        "logs each attempt",
        "backoff is not exponential",
    )


def test_carry_items_do_not_duplicate_an_existing_criterion() -> None:
    contract = build_contract_from_step(
        _step(),
        task_id="t-1",
        sprint_number=2,
        goal="fix the retry",
        verification_steps=(),
        carry_items=("logs each attempt",),
    )

    assert contract.acceptance_criteria == ("retries three times", "logs each attempt")


def test_step_without_criteria_falls_back_to_its_description() -> None:
    contract = build_contract_from_step(
        _step(acceptance_criteria=()),
        task_id="t-1",
        sprint_number=1,
        goal="ship the retry",
        verification_steps=(),
    )

    assert contract.acceptance_criteria == ("add the retry decorator",)


def test_contract_still_round_trips_on_disk(tmp_path: Path) -> None:
    contract = build_contract_from_step(
        _step(),
        task_id="t-1",
        sprint_number=1,
        goal="ship the retry",
        verification_steps=({"kind": "lint", "command": "ruff"},),
    )
    path = tmp_path / "contract.json"
    contract.save(path)

    assert SprintContract.load(path) == contract
