"""Tests for the evaluation record + outcome→ledger transition.

Spec 10 acceptance criteria #5, #6, #10, plus outcome rules in §"Generator
+ evaluator loop" step 6:

- evaluator writes **exactly one** evaluation record per sprint (#10)
- ``pass`` marks the step ``done``
- ``needs-changes`` keeps the step ``in_progress`` and surfaces items
  for the next contract
- ``fail`` marks the step ``blocked`` and appends a tech-debt entry
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# --- evaluation record shape -------------------------------------------


def test_evaluation_record_round_trips_via_to_dict_from_dict() -> None:
    from dream.sprint import EvaluationRecord

    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=2,
        step_id="s1",
        outcome="needs-changes",
        score=0.6,
        notes="missing X",
        items=("redo X", "add test for Y"),
        evaluator_version="v1",
    )
    assert EvaluationRecord.from_dict(rec.to_dict()) == rec


@pytest.mark.parametrize("bad", ["passed", "yes", "rejected", ""])
def test_evaluation_record_rejects_unknown_outcome(bad: str) -> None:
    from dream.sprint import EvaluationRecord

    with pytest.raises(ValueError, match="outcome"):
        EvaluationRecord(
            task_id="t1",
            sprint_number=1,
            step_id="s1",
            outcome=bad,  # type: ignore[arg-type]
        )


def test_evaluation_record_path_under_docs_evals(tmp_path: Path) -> None:
    from dream.sprint import evaluation_record_path

    p = evaluation_record_path(tmp_path, task_id="t1", sprint_number=3)
    assert p == tmp_path / "docs" / "evals" / "t1" / "sprint-3.json"


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "/abs", "a\x00b"])
def test_evaluation_record_path_rejects_unsafe_task_id(tmp_path: Path, bad: str) -> None:
    from dream.sprint import evaluation_record_path

    with pytest.raises(ValueError, match="task_id|unsafe"):
        evaluation_record_path(tmp_path, task_id=bad, sprint_number=1)


# --- write-once behaviour ---------------------------------------------


def test_record_evaluation_writes_record_file_atomically(tmp_path: Path) -> None:
    from dream.sprint import EvaluationRecord, evaluation_record_path, record_evaluation

    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="pass",
    )
    path = record_evaluation(tmp_path, rec)
    assert path == evaluation_record_path(tmp_path, task_id="t1", sprint_number=1)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["outcome"] == "pass"
    assert data["task_id"] == "t1"


def test_record_evaluation_refuses_duplicate_for_same_sprint(tmp_path: Path) -> None:
    """Criterion #10: 'exactly one evaluation record per sprint when enabled.'"""
    from dream.sprint import EvaluationAlreadyRecorded, EvaluationRecord, record_evaluation

    rec = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="s1", outcome="pass"
    )
    record_evaluation(tmp_path, rec)
    with pytest.raises(EvaluationAlreadyRecorded):
        record_evaluation(tmp_path, rec)


# --- outcome → ledger -------------------------------------------------


def _make_ledger():
    from dream.planner import LedgerStep, PlannerLedger

    return PlannerLedger(
        task_id="t1",
        intent="ship widget",
        created_at=0.0,
        steps=(
            LedgerStep(id="s1", description="A", status="in_progress"),
            LedgerStep(id="s2", description="B", status="pending"),
        ),
    )


def test_pass_outcome_marks_step_done() -> None:
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _make_ledger()
    rec = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="s1", outcome="pass"
    )
    updated = apply_outcome(ledger, rec)
    by_id = {s.id: s for s in updated.steps}
    assert by_id["s1"].status == "done"
    assert by_id["s2"].status == "pending"


def test_needs_changes_keeps_step_in_progress() -> None:
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _make_ledger()
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="needs-changes",
        items=("redo X", "tighten Y"),
    )
    updated = apply_outcome(ledger, rec)
    by_id = {s.id: s for s in updated.steps}
    assert by_id["s1"].status == "in_progress"


def test_needs_changes_items_retrievable_for_next_contract(tmp_path: Path) -> None:
    """The runner needs to seed the next negotiate_contract call with these."""
    from dream.sprint import (
        EvaluationRecord,
        load_pending_carry_items,
        record_evaluation,
    )

    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="needs-changes",
        items=("redo X", "tighten Y"),
    )
    record_evaluation(tmp_path, rec)
    items = load_pending_carry_items(tmp_path, task_id="t1", step_id="s1")
    assert items == ("redo X", "tighten Y")


def test_carry_items_only_returned_when_latest_eval_is_needs_changes(tmp_path: Path) -> None:
    from dream.sprint import EvaluationRecord, load_pending_carry_items, record_evaluation

    record_evaluation(
        tmp_path,
        EvaluationRecord(task_id="t1", sprint_number=1, step_id="s1", outcome="pass"),
    )
    items = load_pending_carry_items(tmp_path, task_id="t1", step_id="s1")
    assert items == ()


def test_fail_outcome_marks_step_blocked() -> None:
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _make_ledger()
    rec = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="s1", outcome="fail",
        notes="unsalvageable", items=("can't reconcile spec",),
    )
    updated = apply_outcome(ledger, rec)
    by_id = {s.id: s for s in updated.steps}
    assert by_id["s1"].status == "blocked"


def test_apply_outcome_raises_on_unknown_step_id() -> None:
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _make_ledger()
    rec = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="nope", outcome="pass"
    )
    with pytest.raises(KeyError, match="nope"):
        apply_outcome(ledger, rec)


# --- tech-debt ledger ---------------------------------------------------


def test_fail_outcome_files_tech_debt_entry(tmp_path: Path) -> None:
    from dream.sprint import EvaluationRecord, append_tech_debt, tech_debt_path

    rec = EvaluationRecord(
        task_id="t1", sprint_number=2, step_id="s1", outcome="fail",
        notes="couldn't ship", items=("verify bar",),
    )
    path = append_tech_debt(tmp_path, rec)
    assert path == tech_debt_path(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "t1" in text
    assert "sprint 2" in text or "sprint-2" in text
    assert "s1" in text
    assert "couldn't ship" in text


def test_tech_debt_append_preserves_prior_entries(tmp_path: Path) -> None:
    from dream.sprint import EvaluationRecord, append_tech_debt

    r1 = EvaluationRecord(task_id="t1", sprint_number=1, step_id="sA", outcome="fail", notes="r1")
    r2 = EvaluationRecord(task_id="t2", sprint_number=1, step_id="sB", outcome="fail", notes="r2")
    append_tech_debt(tmp_path, r1)
    append_tech_debt(tmp_path, r2)
    text = (tmp_path / "docs" / "exec-plans" / "tech-debt-tracker.md").read_text("utf-8")
    assert "r1" in text and "r2" in text
    assert text.index("r1") < text.index("r2")


def test_append_tech_debt_refuses_non_fail_outcome(tmp_path: Path) -> None:
    from dream.sprint import EvaluationRecord, append_tech_debt

    for outcome in ("pass", "needs-changes"):
        rec = EvaluationRecord(
            task_id="t1", sprint_number=1, step_id="s1", outcome=outcome,
        )
        with pytest.raises(ValueError, match="fail"):
            append_tech_debt(tmp_path, rec)
