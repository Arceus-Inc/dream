"""Wave C — carryover refresh from workspace."""

from __future__ import annotations

from pathlib import Path

from dream.planner._artefacts import LedgerStep, PlannerLedger
from dream.services.compact._carryover import refresh_carryover_from_workspace


def test_refresh_carryover_merges_exec_plan_and_blocked_steps(tmp_path: Path) -> None:
    active = tmp_path / "docs" / "exec-plans" / "active"
    active.mkdir(parents=True)
    ledger = PlannerLedger(
        task_id="task-1",
        intent="ship",
        created_at=1.0,
        steps=(
            LedgerStep(id="s1", description="done step", status="done"),
            LedgerStep(id="s2", description="active", status="in_progress"),
            LedgerStep(id="s3", description="stuck", status="blocked", notes="needs api key"),
        ),
    )
    ledger.save(active / "task-1.json")

    carryover: dict = {"working_dir": str(tmp_path)}
    refresh_carryover_from_workspace(tmp_path, carryover)

    assert carryover["exec_plan_filename"] == "task-1.json"
    assert carryover["exec_plan_current_step"] == "s2"
    assert carryover["blocked_steps"] == [{"step_id": "s3", "reason": "needs api key"}]


def test_refresh_carryover_noop_when_no_active_dir(tmp_path: Path) -> None:
    carryover: dict = {}
    refresh_carryover_from_workspace(tmp_path, carryover)
    assert carryover == {}
