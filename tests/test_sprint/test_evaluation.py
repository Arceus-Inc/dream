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

    with pytest.raises(ValueError, match=r"task_id|unsafe"):
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


def test_record_evaluation_does_not_overwrite_when_exists_check_is_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion #10 must hold even under a check-then-write race.

    We simulate the race directly: force the pre-write ``exists()`` probe to
    report False (as both racing writers would see before either rename),
    write a first record, then attempt a second write. A guard that relies
    only on ``exists()`` would silently overwrite the first record; an atomic
    create must refuse the second writer instead.
    """
    from pathlib import Path as _Path

    from dream.sprint import (
        EvaluationAlreadyRecorded,
        EvaluationRecord,
        evaluation_record_path,
        record_evaluation,
    )

    first = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="s1", outcome="pass", notes="first"
    )
    second = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="s1", outcome="fail", notes="second"
    )
    path = evaluation_record_path(tmp_path, task_id="t1", sprint_number=1)

    record_evaluation(tmp_path, first)

    monkeypatch.setattr(_Path, "exists", lambda self: False)
    with pytest.raises(EvaluationAlreadyRecorded):
        record_evaluation(tmp_path, second)

    # The first record must survive untouched.
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["notes"] == "first"
    assert saved["outcome"] == "pass"


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


@pytest.mark.parametrize("stale_status", ["pending", "done", "blocked"])
def test_apply_outcome_rejects_non_in_progress_step(stale_status: str) -> None:
    """Only the active in_progress step may transition. A stale or misrouted
    evaluation record must not move a pending/done/blocked step backward or
    sideways."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = PlannerLedger(
        task_id="t1",
        intent="ship widget",
        created_at=0.0,
        steps=(LedgerStep(id="s1", description="A", status=stale_status),),
    )
    rec = EvaluationRecord(task_id="t1", sprint_number=1, step_id="s1", outcome="pass")
    with pytest.raises(ValueError, match="in_progress"):
        apply_outcome(ledger, rec)


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


def test_append_tech_debt_holds_exclusive_lock_around_read_modify_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-modify-write append must be guarded by an exclusive lock so
    concurrent appenders can't both read the same content and lose an entry.

    We assert the structural fix directly: the lock is held while the file is
    read and written, so the critical section is serialized cross-process.
    """
    import contextlib

    from dream.sprint import EvaluationRecord, _outcome, append_tech_debt

    events: list[str] = []

    @contextlib.contextmanager
    def fake_lock(lock_path, *args, **kwargs):  # type: ignore[no-untyped-def]
        events.append("lock-acquire")
        try:
            yield
        finally:
            events.append("lock-release")

    real_read_text = _outcome.Path.read_text

    def spy_read_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.name == "tech-debt-tracker.md":
            events.append("read")
        return real_read_text(self, *args, **kwargs)

    real_write = _outcome.atomic_write_text

    def spy_write(path, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        events.append("write")
        return real_write(path, text, *args, **kwargs)

    # Seed so the append exercises the read-modify-write (read branch).
    append_tech_debt(
        tmp_path,
        EvaluationRecord(
            task_id="t0", sprint_number=1, step_id="s0", outcome="fail", notes="seed"
        ),
    )

    monkeypatch.setattr(_outcome, "exclusive_file_lock", fake_lock)
    monkeypatch.setattr(_outcome.Path, "read_text", spy_read_text)
    monkeypatch.setattr(_outcome, "atomic_write_text", spy_write)

    rec = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="sA", outcome="fail", notes="x"
    )
    append_tech_debt(tmp_path, rec)

    # read + write must both fall strictly inside the lock window.
    assert events[0] == "lock-acquire"
    assert events[-1] == "lock-release"
    assert "read" in events and "write" in events
    assert events.index("lock-acquire") < events.index("write")
    assert events.index("write") < events.index("lock-release")


def test_append_tech_debt_refuses_non_fail_outcome(tmp_path: Path) -> None:
    from dream.sprint import EvaluationRecord, append_tech_debt

    for outcome in ("pass", "needs-changes"):
        rec = EvaluationRecord(
            task_id="t1", sprint_number=1, step_id="s1", outcome=outcome,
        )
        with pytest.raises(ValueError, match="fail"):
            append_tech_debt(tmp_path, rec)


# --- Fix 2: notes carry-through + N-strikes --------------------------------


def _in_progress_ledger():
    """Return a ledger whose first step is in_progress, ready for evaluation."""
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


def test_needs_changes_first_strike_stays_in_progress_with_count_one() -> None:
    """First needs-changes: step stays in_progress, count becomes 1."""
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _in_progress_ledger()
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="needs-changes",
        notes="add docstring",
    )
    updated = apply_outcome(ledger, rec)
    step = next(s for s in updated.steps if s.id == "s1")
    assert step.status == "in_progress"
    assert step.needs_changes_count == 1


def test_needs_changes_first_strike_appends_evaluator_notes() -> None:
    """Evaluator notes are injected into step.notes so the generator retries
    with context rather than an identical prompt."""
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _in_progress_ledger()
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="needs-changes",
        notes="missing docstring",
    )
    updated = apply_outcome(ledger, rec)
    step = next(s for s in updated.steps if s.id == "s1")
    assert "[evaluator, sprint 1]" in step.notes
    assert "missing docstring" in step.notes


def test_needs_changes_appends_to_existing_notes() -> None:
    """When the step already has notes, the evaluator feedback is separated by
    a newline and appended — prior context is preserved."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.sprint import EvaluationRecord, apply_outcome

    step = LedgerStep(
        id="s1",
        description="A",
        status="in_progress",
        notes="original guidance",
    )
    ledger = PlannerLedger(
        task_id="t1",
        intent="x",
        created_at=0.0,
        steps=(step,),
    )
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=2,
        step_id="s1",
        outcome="needs-changes",
        notes="add types",
    )
    updated = apply_outcome(ledger, rec)
    updated_step = updated.steps[0]
    # Both segments present, separated by newline
    assert "original guidance" in updated_step.notes
    assert "[evaluator, sprint 2]" in updated_step.notes
    assert "add types" in updated_step.notes
    assert updated_step.notes.index("original guidance") < updated_step.notes.index("[evaluator")


def test_needs_changes_second_strike_becomes_blocked() -> None:
    """On the second needs-changes (NEEDS_CHANGES_LIMIT = 2) the step is blocked
    so the budget is not burned with further retries."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.sprint import EvaluationRecord, apply_outcome

    step = LedgerStep(
        id="s1",
        description="A",
        status="in_progress",
        needs_changes_count=1,  # already had one strike
    )
    ledger = PlannerLedger(
        task_id="t1",
        intent="x",
        created_at=0.0,
        steps=(step,),
    )
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=2,
        step_id="s1",
        outcome="needs-changes",
        notes="still missing types",
    )
    updated = apply_outcome(ledger, rec)
    updated_step = updated.steps[0]
    assert updated_step.status == "blocked"
    assert updated_step.needs_changes_count == 2


def test_needs_changes_second_strike_carries_both_notes() -> None:
    """After two strikes both evaluator messages must be readable in step.notes."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.sprint import EvaluationRecord, apply_outcome

    step = LedgerStep(
        id="s1",
        description="A",
        status="in_progress",
        notes="[evaluator, sprint 1] first complaint",
        needs_changes_count=1,
    )
    ledger = PlannerLedger(task_id="t1", intent="x", created_at=0.0, steps=(step,))
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=2,
        step_id="s1",
        outcome="needs-changes",
        notes="second complaint",
    )
    updated = apply_outcome(ledger, rec)
    notes = updated.steps[0].notes
    assert "first complaint" in notes
    assert "[evaluator, sprint 2]" in notes
    assert "second complaint" in notes


def test_needs_changes_empty_record_notes_appends_nothing_but_increments_count() -> None:
    """When the evaluator leaves notes empty, step.notes is unchanged (no
    extra separator) but the counter still increments."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.sprint import EvaluationRecord, apply_outcome

    step = LedgerStep(
        id="s1",
        description="A",
        status="in_progress",
        notes="existing note",
    )
    ledger = PlannerLedger(task_id="t1", intent="x", created_at=0.0, steps=(step,))
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="needs-changes",
        notes="",  # empty
    )
    updated = apply_outcome(ledger, rec)
    updated_step = updated.steps[0]
    assert updated_step.needs_changes_count == 1
    # notes unchanged — no separator or evaluator tag appended
    assert updated_step.notes == "existing note"


def test_pass_outcome_unchanged_behaviour() -> None:
    """pass → done; notes and count untouched (regression guard)."""
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _in_progress_ledger()
    rec = EvaluationRecord(
        task_id="t1", sprint_number=1, step_id="s1", outcome="pass"
    )
    updated = apply_outcome(ledger, rec)
    step = next(s for s in updated.steps if s.id == "s1")
    assert step.status == "done"


def test_fail_outcome_carries_evaluator_notes_into_step() -> None:
    """fail → blocked AND evaluator notes appended — the blocked step's reason
    must be readable without inspecting the tech-debt file."""
    from dream.sprint import EvaluationRecord, apply_outcome

    ledger = _in_progress_ledger()
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=3,
        step_id="s1",
        outcome="fail",
        notes="unsalvageable approach",
    )
    updated = apply_outcome(ledger, rec)
    step = next(s for s in updated.steps if s.id == "s1")
    assert step.status == "blocked"
    assert "[evaluator, sprint 3]" in step.notes
    assert "unsalvageable approach" in step.notes


def test_fail_outcome_with_empty_notes_does_not_append() -> None:
    """fail with no evaluator notes: step is blocked but notes stay unchanged."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.sprint import EvaluationRecord, apply_outcome

    step = LedgerStep(id="s1", description="A", status="in_progress", notes="prior note")
    ledger = PlannerLedger(task_id="t1", intent="x", created_at=0.0, steps=(step,))
    rec = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="fail",
        notes="",
    )
    updated = apply_outcome(ledger, rec)
    updated_step = updated.steps[0]
    assert updated_step.status == "blocked"
    assert updated_step.notes == "prior note"
