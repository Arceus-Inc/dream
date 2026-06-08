"""Tests for the planner artefacts (spec/ledger) and their path helpers.

Spec 10 §"Task start (planner)" + acceptance criteria #1-#4. The planner
ships two committed files per task into the worktree's exec-plans/active
folder; this module covers the data shape, path computation, and the
atomic-write contract on the ledger.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_ledger_step_round_trips_via_to_dict_from_dict() -> None:
    from dream.planner import LedgerStep

    step = LedgerStep(
        id="step-1",
        description="Read the brief and survey the repo.",
        status="pending",
        sprint_target=1,
        notes="must-read: docs/core-beliefs.md",
    )
    rt = LedgerStep.from_dict(step.to_dict())
    assert rt == step


def test_ledger_step_rejects_unknown_status() -> None:
    from dream.planner import LedgerStep

    with pytest.raises(ValueError, match="status"):
        LedgerStep(id="x", description="y", status="not-a-status")  # type: ignore[arg-type]


def test_ledger_step_default_status_is_pending() -> None:
    from dream.planner import LedgerStep

    s = LedgerStep(id="x", description="y")
    assert s.status == "pending"


def test_planner_ledger_round_trips_via_to_dict_from_dict() -> None:
    from dream.planner import LedgerStep, PlannerLedger

    ledger = PlannerLedger(
        task_id="abc-1",
        intent="Add a foo widget.",
        created_at=1.5,
        steps=(
            LedgerStep(id="s1", description="A"),
            LedgerStep(id="s2", description="B", status="in_progress"),
        ),
        evaluator_enabled=False,
    )
    rt = PlannerLedger.from_dict(ledger.to_dict())
    assert rt == ledger


def test_planner_ledger_save_writes_json_file(tmp_path: Path) -> None:
    from dream.planner import LedgerStep, PlannerLedger

    ledger = PlannerLedger(
        task_id="abc-1",
        intent="hello",
        created_at=42.0,
        steps=(LedgerStep(id="s1", description="A"),),
    )
    path = tmp_path / "abc-1.json"
    ledger.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task_id"] == "abc-1"
    assert data["intent"] == "hello"
    assert data["steps"][0]["id"] == "s1"
    assert data["evaluator_enabled"] is True


def test_planner_ledger_load_round_trips_from_disk(tmp_path: Path) -> None:
    from dream.planner import LedgerStep, PlannerLedger

    ledger = PlannerLedger(
        task_id="abc-1",
        intent="hello",
        created_at=42.0,
        steps=(LedgerStep(id="s1", description="A"),),
    )
    path = tmp_path / "abc-1.json"
    ledger.save(path)
    assert PlannerLedger.load(path) == ledger


def test_planner_ledger_save_creates_parent_directories(tmp_path: Path) -> None:
    from dream.planner import PlannerLedger

    ledger = PlannerLedger(task_id="abc-1", intent="x", created_at=0.0)
    nested = tmp_path / "docs" / "exec-plans" / "active" / "abc-1.json"
    ledger.save(nested)
    assert nested.exists()


def test_planner_spec_path_is_under_exec_plans_active(tmp_path: Path) -> None:
    from dream.planner import planner_spec_path

    p = planner_spec_path(tmp_path, "task-42")
    assert p == tmp_path / "docs" / "exec-plans" / "active" / "task-42.md"


def test_planner_ledger_path_is_under_exec_plans_active(tmp_path: Path) -> None:
    from dream.planner import planner_ledger_path

    p = planner_ledger_path(tmp_path, "task-42")
    assert p == tmp_path / "docs" / "exec-plans" / "active" / "task-42.json"


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "/abs", "a\x00b"])
def test_planner_paths_reject_unsafe_task_id(tmp_path: Path, bad: str) -> None:
    from dream.planner import planner_ledger_path, planner_spec_path

    with pytest.raises(ValueError, match="task_id|unsafe"):
        planner_spec_path(tmp_path, bad)
    with pytest.raises(ValueError, match="task_id|unsafe"):
        planner_ledger_path(tmp_path, bad)
