"""Spec 07 slice 2 — runtime task record (``TaskRecord``).

The runtime task record is the *ephemeral* half of Spec 07's task engine:
the in-memory handle for a subprocess or session that the
:class:`BackgroundTaskManager` spawns, supervises, streams output for, and
reaps. Unlike OpenHarness's mutable dataclass we keep it **frozen** —
every transition (``with_status``, ``with_return_code``, ``with_started``,
``with_ended``) returns a new record, matching the rest of the Dream
codebase (``Ledger`` / ``WakeSource`` / ``HeartbeatState``). The manager
holds the canonical mapping ``id -> TaskRecord`` and rebinds it on each
transition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._types import TaskRecord, TaskStatus, TaskType


def _record(**overrides: object) -> TaskRecord:
    base: dict[str, object] = dict(
        id="task-1",
        type="local_bash",
        status="pending",
        description="a test task",
        cwd=".",
        output_file=Path("out.log"),
    )
    base.update(overrides)
    return TaskRecord(**base)  # type: ignore[arg-type]


# --- typing & literals ------------------------------------------------------


def test_task_type_literal_values() -> None:
    """The five task-type tags are part of Spec 07's runtime contract (#13)."""
    assert set(TaskType.__args__) == {  # type: ignore[attr-defined]
        "local_bash",
        "local_agent",
        "remote_agent",
        "in_process_teammate",
        "dream",
    }


def test_task_status_literal_values() -> None:
    """pending -> running -> {completed | failed | killed} is the FSM (#13)."""
    assert set(TaskStatus.__args__) == {  # type: ignore[attr-defined]
        "pending",
        "running",
        "completed",
        "failed",
        "killed",
    }


# --- shape ------------------------------------------------------------------


def test_task_record_required_fields() -> None:
    r = _record()
    assert r.id == "task-1"
    assert r.type == "local_bash"
    assert r.status == "pending"
    assert r.description == "a test task"
    assert r.cwd == "."
    assert r.output_file == Path("out.log")


def test_task_record_optional_defaults() -> None:
    r = _record()
    assert r.command is None
    assert r.prompt is None
    assert r.started_at is None
    assert r.ended_at is None
    assert r.return_code is None
    assert r.env is None
    assert r.argv is None
    assert r.metadata == {}


def test_task_record_is_frozen() -> None:
    """Records are immutable — transitions go through ``with_*`` helpers."""
    r = _record()
    with pytest.raises((AttributeError, TypeError)):
        setattr(r, "status", "running")


# --- transition helpers -----------------------------------------------------


def test_with_status_returns_new_record() -> None:
    r = _record()
    r2 = r.with_status("running")
    assert r2.status == "running"
    assert r.status == "pending"  # original untouched


def test_with_return_code_records_exit() -> None:
    r = _record().with_status("running")
    r2 = r.with_return_code(0)
    assert r2.return_code == 0
    assert r2.status == "running"  # status is a separate concern


def test_with_started_sets_started_at() -> None:
    r = _record()
    r2 = r.with_started(123.0)
    assert r2.started_at == 123.0
    assert r.started_at is None


def test_with_ended_sets_ended_at() -> None:
    r = _record()
    r2 = r.with_ended(456.0)
    assert r2.ended_at == 456.0


def test_with_metadata_merges_and_does_not_mutate() -> None:
    """metadata is a dict, but with_metadata always returns a new record."""
    r = _record(metadata={"task_id": "T1"})
    r2 = r.with_metadata({"entry_id": "e1"})
    assert r2.metadata == {"task_id": "T1", "entry_id": "e1"}
    assert r.metadata == {"task_id": "T1"}


# --- task taxonomy guardrails ----------------------------------------------


def test_task_record_status_transitions_only_through_helpers() -> None:
    """End-to-end FSM walk used by the manager: pending -> running -> completed."""
    r = _record()
    r = r.with_status("running").with_started(1.0)
    r = r.with_status("completed").with_ended(2.0).with_return_code(0)
    assert r.status == "completed"
    assert r.return_code == 0
    assert r.started_at == 1.0
    assert r.ended_at == 2.0
