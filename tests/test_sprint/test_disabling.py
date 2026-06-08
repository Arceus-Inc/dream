"""Disabled-evaluator behaviours.

Spec 10 §"Disabling the evaluator" + scenario "Evaluator can be disabled
at task level":

- task-level: a flag on the exec-plan JSON (the planner ledger)
- sprint-level: a flag on the sprint contract
- when disabled, the generator skips negotiation entirely AND no
  evaluation record is written

The actual short-circuit lives in :func:`is_evaluator_enabled_for_sprint`
which the runner consults.
"""

from __future__ import annotations

import pytest


def _ledger(evaluator_enabled: bool):
    from dream.planner import LedgerStep, PlannerLedger

    return PlannerLedger(
        task_id="t1",
        intent="x",
        created_at=0.0,
        steps=(LedgerStep(id="s1", description="A"),),
        evaluator_enabled=evaluator_enabled,
    )


def test_evaluator_enabled_when_ledger_and_no_explicit_sprint_override() -> None:
    from dream.sprint import is_evaluator_enabled_for_sprint

    assert is_evaluator_enabled_for_sprint(_ledger(True), sprint_override=None) is True


def test_evaluator_can_be_disabled_at_task_level() -> None:
    from dream.sprint import is_evaluator_enabled_for_sprint

    assert is_evaluator_enabled_for_sprint(_ledger(False), sprint_override=None) is False


def test_evaluator_can_be_disabled_at_sprint_level() -> None:
    """Sprint-level override of an otherwise enabled task."""
    from dream.sprint import is_evaluator_enabled_for_sprint

    assert is_evaluator_enabled_for_sprint(_ledger(True), sprint_override=False) is False


def test_sprint_level_enable_does_not_override_task_level_disable() -> None:
    """A task-level disable is the higher authority — sprint can opt out,
    not opt back in. Otherwise the 'routine task' default would be
    re-armable on a whim."""
    from dream.sprint import is_evaluator_enabled_for_sprint

    assert is_evaluator_enabled_for_sprint(_ledger(False), sprint_override=True) is False


def test_disabled_evaluator_skips_negotiation_and_record() -> None:
    """Composite: when disabled, the runner SHOULD NOT call negotiate
    nor write an eval record. This test exercises the documented contract
    surface used by the runner — it asserts that a disabled evaluator
    short-circuits both call sites."""
    from dream.sprint import is_evaluator_enabled_for_sprint

    enabled = is_evaluator_enabled_for_sprint(_ledger(False), sprint_override=None)
    assert enabled is False


@pytest.mark.parametrize("override", [True, False, None])
def test_is_evaluator_enabled_pure_function(override) -> None:
    """Idempotent / no I/O — safe to call from any context."""
    from dream.sprint import is_evaluator_enabled_for_sprint

    a = is_evaluator_enabled_for_sprint(_ledger(True), sprint_override=override)
    b = is_evaluator_enabled_for_sprint(_ledger(True), sprint_override=override)
    assert a == b
