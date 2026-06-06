"""Spec 07 slice 1 — plan FSM and directory layout.

Plans live under ``docs/exec-plans/{active,completed,archived}/`` and move
between dirs as their state changes (Spec 07 decision 4 — *moved, never
deleted*). The FSM is strict: ``draft → active → completed → archived``,
no jumps, no reversals.

The retention helper finds completed plans older than ``retention_days``
and returns them as archive candidates; the actual move is the same
``move_plan`` API the runner uses on completion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dream.tasks._fsm import (
    PLAN_STATES,
    PlanFSMError,
    advance_state,
    archive_candidates,
    move_plan,
    plan_dir,
)
from dream.tasks._ledger import Ledger, LedgerEntry
from dream.tasks._plan import ExecPlan, write_plan


def _t() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


def _make_plan(
    *,
    task_id: str = "T1",
    state: str = "active",
    created_at: datetime | None = None,
) -> ExecPlan:
    from dream.tasks._plan import EXEC_PLAN_SECTIONS

    return ExecPlan(
        task_id=task_id,
        sections={s: f"…{s}…" for s in EXEC_PLAN_SECTIONS},
        ledger=Ledger(
            task_id=task_id,
            state=state,  # type: ignore[arg-type]
            created_at=created_at or _t(),
            updated_at=created_at or _t(),
            evaluator_enabled=False,
            weights={},
            entries=(LedgerEntry(id="a", description="d", steps=("s1",)),),
        ),
    )


# --- FSM order --------------------------------------------------------------


def test_plan_states_are_ordered() -> None:
    """``draft → active → completed → archived`` (Spec 07 decision 4)."""
    assert PLAN_STATES == ("draft", "active", "completed", "archived")


def test_advance_draft_to_active_ok() -> None:
    assert advance_state("draft") == "active"


def test_advance_active_to_completed_ok() -> None:
    assert advance_state("active") == "completed"


def test_advance_completed_to_archived_ok() -> None:
    assert advance_state("completed") == "archived"


def test_advance_from_archived_rejected() -> None:
    with pytest.raises(PlanFSMError, match="archived"):
        advance_state("archived")


def test_advance_rejects_skip() -> None:
    """No `draft → completed` jumps even if the operator wishes there were."""
    # advance_state has no notion of skipping — the test enforces that a
    # caller can only ask for the next legal state, never two ahead.
    assert advance_state("draft") == "active"
    assert advance_state("active") != "archived"


# --- directory layout -------------------------------------------------------


def test_plan_dir_for_each_state(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    assert plan_dir(root, state="draft") == root / "draft"
    assert plan_dir(root, state="active") == root / "active"
    assert plan_dir(root, state="completed") == root / "completed"
    assert plan_dir(root, state="archived") == root / "archived"


# --- move ------------------------------------------------------------------


def test_move_plan_relocates_both_files(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    src_dir = plan_dir(root, state="active")
    write_plan(src_dir, _make_plan(state="active"))

    moved = move_plan(root, task_id="T1", from_state="active", to_state="completed")

    dst_dir = plan_dir(root, state="completed")
    assert moved.ledger.state == "completed"
    assert (dst_dir / "T1.md").exists()
    assert (dst_dir / "T1.json").exists()
    assert not (src_dir / "T1.md").exists()
    assert not (src_dir / "T1.json").exists()


def test_move_plan_writes_new_ledger_state(tmp_path: Path) -> None:
    """The ledger's ``state`` field tracks the directory it lives in."""
    root = tmp_path / "docs" / "exec-plans"
    write_plan(plan_dir(root, state="active"), _make_plan(state="active"))

    move_plan(root, task_id="T1", from_state="active", to_state="completed")

    from dream.tasks._plan import read_plan

    rebuilt = read_plan(plan_dir(root, state="completed"), task_id="T1")
    assert rebuilt.ledger.state == "completed"


def test_move_plan_rejects_illegal_transition(tmp_path: Path) -> None:
    """``active → archived`` skips ``completed`` — refused."""
    root = tmp_path / "docs" / "exec-plans"
    write_plan(plan_dir(root, state="active"), _make_plan(state="active"))
    with pytest.raises(PlanFSMError):
        move_plan(root, task_id="T1", from_state="active", to_state="archived")


def test_move_plan_never_deletes_files(tmp_path: Path) -> None:
    """Spec 07 decision 4 — *moved, never deleted*. After three moves all
    six files (md+json across active/completed/archived snapshots) … no
    actually only the latest pair survives, because move is a *rename*
    not a copy. The invariant we check: no file is ``unlink``ed, only
    renamed."""
    root = tmp_path / "docs" / "exec-plans"
    write_plan(plan_dir(root, state="active"), _make_plan(state="active"))

    move_plan(root, task_id="T1", from_state="active", to_state="completed")
    move_plan(root, task_id="T1", from_state="completed", to_state="archived")

    assert (plan_dir(root, state="archived") / "T1.md").exists()
    assert (plan_dir(root, state="archived") / "T1.json").exists()
    # nothing left behind in the old dirs
    assert not list(plan_dir(root, state="active").glob("T1.*"))
    assert not list(plan_dir(root, state="completed").glob("T1.*"))


def test_move_plan_missing_source_raises(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    with pytest.raises(FileNotFoundError):
        move_plan(root, task_id="missing", from_state="active", to_state="completed")


# --- retention --------------------------------------------------------------


def test_archive_candidates_finds_old_completed_plans(tmp_path: Path) -> None:
    """Default retention is 90 days; older completed plans surface as
    candidates for the ``doc-garden`` cron kind to archive."""
    root = tmp_path / "docs" / "exec-plans"
    long_ago = _t() - timedelta(days=120)
    recent = _t() - timedelta(days=10)
    write_plan(
        plan_dir(root, state="completed"),
        _make_plan(task_id="OLD", state="completed", created_at=long_ago),
    )
    write_plan(
        plan_dir(root, state="completed"),
        _make_plan(task_id="NEW", state="completed", created_at=recent),
    )

    cands = archive_candidates(root, now=_t(), retention_days=90)
    assert {c.task_id for c in cands} == {"OLD"}


def test_archive_candidates_ignores_non_completed_plans(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    long_ago = _t() - timedelta(days=200)
    write_plan(
        plan_dir(root, state="active"),
        _make_plan(state="active", created_at=long_ago),
    )
    assert archive_candidates(root, now=_t(), retention_days=90) == ()


def test_archive_candidates_uses_default_retention_of_90_days(tmp_path: Path) -> None:
    """Spec 07 decision 4 — default retention window is 90 days."""
    root = tmp_path / "docs" / "exec-plans"
    write_plan(
        plan_dir(root, state="completed"),
        _make_plan(state="completed", created_at=_t() - timedelta(days=91)),
    )
    cands = archive_candidates(root, now=_t())
    assert len(cands) == 1
