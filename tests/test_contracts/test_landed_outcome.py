"""TDD: LandedPhase / LandedOutcome — the cross-repo landed-outcome seam (Phase 0)."""

from __future__ import annotations

import pytest

from dream.contracts.strategy import (
    LandedOutcome,
    LandedPhase,
    OutcomeEvent,
    RecoveryHint,
)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (LandedPhase.TERMINAL_PASS, True),
        (LandedPhase.TERMINAL_FAIL, False),
        (LandedPhase.NEEDS_REWORK, False),
        (LandedPhase.DELEGATED, None),
        (LandedPhase.STRANDED, None),
        (LandedPhase.CANCELLED, None),
    ],
)
def test_strategy_passed_by_phase(phase: LandedPhase, expected: bool | None) -> None:
    landed = LandedOutcome(phase=phase, summary="s")
    assert landed.strategy_passed() is expected


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (LandedPhase.TERMINAL_PASS, RecoveryHint.NONE),
        (LandedPhase.TERMINAL_FAIL, RecoveryHint.NONE),
        (LandedPhase.NEEDS_REWORK, RecoveryHint.REWORK),
        (LandedPhase.DELEGATED, RecoveryHint.WAIT_FOR_CHILDREN),
        (LandedPhase.STRANDED, RecoveryHint.ESCALATE),
        (LandedPhase.CANCELLED, RecoveryHint.NONE),
    ],
)
def test_recovery_hint_by_phase(phase: LandedPhase, expected: RecoveryHint) -> None:
    landed = LandedOutcome(phase=phase, summary="s")
    assert landed.recovery_hint() is expected


def test_to_dict_from_dict_round_trip() -> None:
    landed = LandedOutcome(
        phase=LandedPhase.NEEDS_REWORK,
        dod_status="failed",
        disposition="dod_failed",
        diagnostic="evaluator: missing test",
        execution_mode="delivery",
        summary="DoD failed — retry",
    )
    restored = LandedOutcome.from_dict(landed.to_dict())
    assert restored == landed


def test_from_dict_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        LandedOutcome.from_dict({"phase": "not_a_phase"})


def test_from_dict_requires_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        LandedOutcome.from_dict({})


def test_outcome_event_accepts_landed_fields() -> None:
    event = OutcomeEvent(
        kind="outcome.landed",
        phase=LandedPhase.DELEGATED.value,
        recovery_hint=RecoveryHint.WAIT_FOR_CHILDREN.value,
        summary="handed off to subtree",
        passed=None,
        detail="decomposed into 3 children",
        task_id="task-1",
    )
    assert event.phase == "delegated"
    assert event.recovery_hint == "wait_for_children"
    assert event.summary == "handed off to subtree"
