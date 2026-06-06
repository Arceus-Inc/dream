"""Spec 07 slice 3 — cron-as-session glue.

Pins the run-record artefact, the completion-listener seam between
:class:`BackgroundTaskManager` and ``docs/cron-runs/``, and the
``spawn_cron_session`` entrypoint that records ``cron.skipped`` when a
manifest is disabled.

Covers:

- :class:`CronRunRecord` round-trips through
  :func:`write_cron_run_record` / :func:`read_cron_run_records` and lands
  under ``{runs_root}/{kind}/{YYYY-MM-DD}-{run_id}.json`` (Spec 07
  §"Cron run-record" / MUST 24).
- :func:`make_cron_run_listener` derives ``success`` / ``failed`` from
  the terminal :class:`TaskRecord`, propagates ``prs_opened`` and the
  ``max_session_minutes`` failure reason (MUST 25).
- :func:`spawn_cron_session` returns ``None`` and writes a ``skipped``
  record when the manifest is disabled (MUST 21).
- :func:`spawn_cron_session` otherwise spawns a ``local_agent`` task
  tagged with cron metadata, runs the wired listener on natural exit,
  and writes the run-record to disk (MUST 23 + 24 surface).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.tasks._cron import CronManifest
from dream.tasks._cron_session import (
    CRON_RUNS_ROOT,
    MAX_SESSION_MINUTES_METADATA_KEY,
    CronRunRecord,
    cron_run_record_path,
    make_cron_run_listener,
    read_cron_run_records,
    spawn_cron_session,
    write_cron_run_record,
)
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._types import TaskRecord


def _py_argv(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def _wait_until_done(
    manager: BackgroundTaskManager, task_id: str, *, timeout: float = 10.0
) -> TaskRecord:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        task = manager.get_task(task_id)
        assert task is not None
        if task.status in {"completed", "failed", "killed"}:
            return task
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task {task_id} did not finish")
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_cron_runs_root_default() -> None:
    assert CRON_RUNS_ROOT == "docs/cron-runs"


# ---------------------------------------------------------------------------
# CronRunRecord I/O
# ---------------------------------------------------------------------------


def test_cron_run_record_path_segments_by_date_and_kind(tmp_path: Path) -> None:
    started = datetime(2024, 7, 4, 6, 30, tzinfo=UTC)
    p = cron_run_record_path(
        tmp_path, kind="doc-garden", run_id="abc123", started_at=started
    )
    assert p == tmp_path / "doc-garden" / "2024-07-04-abc123.json"


def test_write_cron_run_record_round_trip(tmp_path: Path) -> None:
    started = datetime(2024, 7, 4, 6, 30, tzinfo=UTC)
    ended = datetime(2024, 7, 4, 6, 31, tzinfo=UTC)
    record = CronRunRecord(
        kind="doc-garden",
        run_id="abc123",
        started_at=started,
        ended_at=ended,
        outcome="success",
        prs_opened=("https://github.com/org/repo/pull/1",),
        session_jsonl="docs/sessions/2024-07-04-abc123.jsonl",
    )
    target = write_cron_run_record(tmp_path, record)
    assert target.exists()
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["kind"] == "doc-garden"
    assert parsed["outcome"] == "success"
    assert parsed["prs_opened"] == ["https://github.com/org/repo/pull/1"]


def test_read_cron_run_records_returns_chronological(tmp_path: Path) -> None:
    earlier = datetime(2024, 1, 1, 6, 0, tzinfo=UTC)
    later = datetime(2024, 1, 2, 6, 0, tzinfo=UTC)
    for ts, rid in [(earlier, "aa"), (later, "bb")]:
        write_cron_run_record(
            tmp_path,
            CronRunRecord(
                kind="doc-garden",
                run_id=rid,
                started_at=ts,
                outcome="success",
            ),
        )
    rows = read_cron_run_records(tmp_path, "doc-garden")
    assert [r.run_id for r in rows] == ["aa", "bb"]


def test_read_cron_run_records_empty_when_missing(tmp_path: Path) -> None:
    assert read_cron_run_records(tmp_path / "nope", "doc-garden") == []


# ---------------------------------------------------------------------------
# make_cron_run_listener
# ---------------------------------------------------------------------------


def _make_terminal_task(
    *,
    status: str,
    return_code: int | None,
    metadata: dict[str, str] | None = None,
) -> TaskRecord:
    now = time.time()
    return TaskRecord(
        id="local_agent-deadbeef",
        type="local_agent",
        status=status,  # type: ignore[arg-type]
        description="cron:doc-garden",
        cwd=".",
        output_file=Path("/dev/null"),
        argv=["true"],
        created_at=now,
        started_at=now,
        ended_at=now + 1.0,
        return_code=return_code,
        metadata=metadata or {},
    )


def test_listener_writes_success_on_clean_exit(tmp_path: Path) -> None:
    started = datetime(2024, 7, 4, 6, 30, tzinfo=UTC)
    listener = make_cron_run_listener(
        runs_root=tmp_path, kind="doc-garden", run_id="rid1", started_at=started
    )
    listener(_make_terminal_task(status="completed", return_code=0))
    rows = read_cron_run_records(tmp_path, "doc-garden")
    assert len(rows) == 1
    assert rows[0].outcome == "success"
    assert rows[0].failure_reason is None
    assert rows[0].ended_at is not None


def test_listener_writes_failed_on_nonzero(tmp_path: Path) -> None:
    started = datetime(2024, 7, 4, 6, 30, tzinfo=UTC)
    listener = make_cron_run_listener(
        runs_root=tmp_path, kind="doc-garden", run_id="rid2", started_at=started
    )
    listener(_make_terminal_task(status="failed", return_code=2))
    rows = read_cron_run_records(tmp_path, "doc-garden")
    assert len(rows) == 1
    assert rows[0].outcome == "failed"
    assert rows[0].failure_reason == "return_code=2"


def test_listener_records_max_session_minutes_overrun(tmp_path: Path) -> None:
    started = datetime(2024, 7, 4, 6, 30, tzinfo=UTC)
    listener = make_cron_run_listener(
        runs_root=tmp_path, kind="doc-garden", run_id="rid3", started_at=started
    )
    listener(
        _make_terminal_task(
            status="killed",
            return_code=-15,
            metadata={MAX_SESSION_MINUTES_METADATA_KEY: "1"},
        )
    )
    rows = read_cron_run_records(tmp_path, "doc-garden")
    assert rows[0].outcome == "failed"
    assert rows[0].failure_reason == "max-session-minutes"


def test_listener_propagates_prs_opened(tmp_path: Path) -> None:
    started = datetime(2024, 7, 4, 6, 30, tzinfo=UTC)
    listener = make_cron_run_listener(
        runs_root=tmp_path, kind="doc-garden", run_id="rid4", started_at=started
    )
    listener(
        _make_terminal_task(
            status="completed",
            return_code=0,
            metadata={"cron.prs_opened": "https://example/p/1, https://example/p/2"},
        )
    )
    rows = read_cron_run_records(tmp_path, "doc-garden")
    assert rows[0].prs_opened == (
        "https://example/p/1",
        "https://example/p/2",
    )


# ---------------------------------------------------------------------------
# spawn_cron_session
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "cron-runs"


@pytest.fixture
def manifest_enabled() -> CronManifest:
    return CronManifest(
        name="doc-garden",
        enabled=True,
        schedule="0 6 * * *",
        tier_required="repo-write",
        max_session_minutes=30,
        entry_prompt="docs/cron/doc-garden.prompt.md",
    )


@pytest.fixture
def manifest_disabled() -> CronManifest:
    return CronManifest(
        name="quality-grade",
        enabled=False,
        schedule="0 7 * * 1",
    )


async def test_spawn_cron_session_disabled_records_skipped(
    tmp_path: Path, runs_root: Path, manifest_disabled: CronManifest
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    result = await spawn_cron_session(
        manager=mgr,
        manifest=manifest_disabled,
        cwd=tmp_path,
        runs_root=runs_root,
        command="echo unused",
    )
    assert result is None
    rows = read_cron_run_records(runs_root, "quality-grade")
    assert len(rows) == 1
    assert rows[0].outcome == "skipped"
    assert rows[0].failure_reason == "disabled"
    # No task created.
    assert mgr.list_tasks() == []


async def test_spawn_cron_session_enabled_spawns_local_agent_and_records_run(
    tmp_path: Path, runs_root: Path, manifest_enabled: CronManifest
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    record = await spawn_cron_session(
        manager=mgr,
        manifest=manifest_enabled,
        cwd=tmp_path,
        runs_root=runs_root,
        argv=_py_argv("print('cron ran')"),
        run_id="run-fixed",
        started_at=datetime(2024, 8, 1, 6, 0, tzinfo=UTC),
    )
    assert record is not None
    assert record.type == "local_agent"
    assert record.metadata["cron.kind"] == "doc-garden"
    assert record.metadata["cron.run_id"] == "run-fixed"
    assert record.metadata["cron.tier_required"] == "repo-write"
    assert record.metadata["cron.max_session_minutes"] == "30"

    await _wait_until_done(mgr, record.id)
    # Give the listener a tick.
    await asyncio.sleep(0.05)

    rows = read_cron_run_records(runs_root, "doc-garden")
    assert len(rows) == 1
    assert rows[0].outcome == "success"
    assert rows[0].run_id == "run-fixed"
    target = cron_run_record_path(
        runs_root,
        kind="doc-garden",
        run_id="run-fixed",
        started_at=datetime(2024, 8, 1, 6, 0, tzinfo=UTC),
    )
    assert target.exists()


async def test_spawn_cron_session_failure_records_failed(
    tmp_path: Path, runs_root: Path, manifest_enabled: CronManifest
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    record = await spawn_cron_session(
        manager=mgr,
        manifest=manifest_enabled,
        cwd=tmp_path,
        runs_root=runs_root,
        argv=_py_argv("import sys; sys.exit(2)"),
    )
    assert record is not None
    await _wait_until_done(mgr, record.id)
    await asyncio.sleep(0.05)
    rows = read_cron_run_records(runs_root, "doc-garden")
    assert rows[0].outcome == "failed"
    assert rows[0].failure_reason == "return_code=2"
