"""Tests for the public exception hierarchy.

Pins the stable ``code`` attributes and the typed ``RunTaskError.phase``
the sibling repos (chorus) branch on without parsing messages.
"""

from __future__ import annotations

import pytest

from dream.errors import DreamError, RunTaskError, TaskCancelled


def test_task_cancelled_is_dream_error_with_stable_code() -> None:
    err = TaskCancelled("budget exhausted")
    assert isinstance(err, DreamError)
    assert err.code == "dream.cancelled"
    assert str(err) == "budget exhausted"


def test_run_task_error_carries_phase_and_cause() -> None:
    cause = ValueError("planner blew up")
    err = RunTaskError("planner failed", phase="plan", cause=cause)
    assert isinstance(err, DreamError)
    assert err.code == "dream.run_task"
    assert err.phase == "plan"
    assert err.cause is cause


@pytest.mark.parametrize("phase", ["plan", "sprint", "evaluate"])
def test_run_task_error_accepts_each_phase(phase: str) -> None:
    err = RunTaskError("boom", phase=phase)  # type: ignore[arg-type]
    assert err.phase == phase
    assert err.cause is None


def test_run_task_error_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        RunTaskError("boom", phase="deploy")  # type: ignore[arg-type]
