"""Spec 07 slice 3 — REPL slash commands for tasks, cron, and plans.

Pins ``/task list|output|stop``, ``/cron list|show|toggle``, and
``/plan list|show`` plus the "not configured" placeholders that mirror
``/tools`` / ``/tool``.

Each command takes its dependency from a new optional slot on
:class:`ReplState`:

- ``task_manager`` -> :class:`BackgroundTaskManager`
- ``cron_registry_path`` -> :class:`pathlib.Path`
- ``plans_root`` -> :class:`pathlib.Path`

Absence prints ``"<x> not configured"`` and the command returns ``True``
(loop continues), matching :func:`dream.repl._chat._cmd_tools`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from dream.api.credentials import Credential
from dream.api.substrate import CompletionResult, HealthReport
from dream.repl._chat import (
    Dispatcher,
    ReplState,
    SubstrateSpec,
    Transcript,
    _slash,
)
from dream.repl._events import EventSink
from dream.tasks import (
    BackgroundTaskManager,
    CronJob,
    ExecPlan,
    Ledger,
    LedgerEntry,
    plan_dir,
    upsert_cron_job,
    write_plan,
)

# --- minimal ok substrate (no LLM calls under test) ----------------------


class _OkSub:
    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, **kwargs: Any) -> CompletionResult:  # pragma: no cover
        return CompletionResult(text="ok", input_tokens=0, output_tokens=0)

    async def stream(self, **kwargs: Any):  # pragma: no cover
        yield "ok"

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def max_window(self) -> int:
        return 8_192

    def health(self) -> HealthReport:
        return HealthReport(state="ok", detail="", latency_ms=1.0)


def _ok_spec(name: str) -> SubstrateSpec:
    return SubstrateSpec(
        name=name,
        model="ok",
        base_url=None,
        max_window=8_192,
        timeout_seconds=5.0,
        credentials=[Credential(label="only", key="ok", substrate=name)],
        builder=lambda _cred: _OkSub(name),
    )


def _make_state(
    tmp_path: Path,
    *,
    task_manager: BackgroundTaskManager | None = None,
    cron_registry_path: Path | None = None,
    plans_root: Path | None = None,
) -> tuple[Dispatcher, Transcript, ReplState, EventSink]:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_ok_spec("primary")], sink)
    state = ReplState(
        stream=True,
        events_path=str(tmp_path / "events.jsonl"),
        registry=None,
        cwd=tmp_path,
        sink=sink,
        task_manager=task_manager,
        cron_registry_path=cron_registry_path,
        plans_root=plans_root,
    )
    return disp, Transcript(), state, sink


async def _wait_until_done(
    manager: BackgroundTaskManager, task_id: str, *, timeout: float = 10.0
) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        task = manager.get_task(task_id)
        assert task is not None
        if task.status in {"completed", "failed", "killed"}:
            return
        if loop.time() > deadline:
            raise AssertionError(f"task {task_id} did not finish")
        await asyncio.sleep(0.05)


def _py_argv(code: str) -> list[str]:
    return [sys.executable, "-c", code]


# --- /task ---------------------------------------------------------------


def test_slash_task_without_manager_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disp, tr, state, _ = _make_state(tmp_path, task_manager=None)
    capsys.readouterr()
    assert _slash("/task list", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "task manager not configured" in out


def test_slash_task_no_arg_prints_usage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    disp, tr, state, _ = _make_state(tmp_path, task_manager=mgr)
    capsys.readouterr()
    assert _slash("/task", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_slash_task_list_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    disp, tr, state, _ = _make_state(tmp_path, task_manager=mgr)
    capsys.readouterr()
    _slash("/task list", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "no tasks" in out.lower()


async def test_slash_task_list_shows_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    record = await mgr.create_shell_task(
        description="hi", cwd=tmp_path, argv=_py_argv("print('done')")
    )
    await _wait_until_done(mgr, record.id)
    disp, tr, state, _ = _make_state(tmp_path, task_manager=mgr)
    capsys.readouterr()
    _slash("/task list", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert record.id in out
    assert "hi" in out
    assert "completed" in out


async def test_slash_task_output_prints_tail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    record = await mgr.create_shell_task(
        description="echo", cwd=tmp_path, argv=_py_argv("print('marker-XYZ')")
    )
    await _wait_until_done(mgr, record.id)
    disp, tr, state, _ = _make_state(tmp_path, task_manager=mgr)
    capsys.readouterr()
    _slash(f"/task output {record.id}", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "marker-XYZ" in out


def test_slash_task_output_unknown_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    disp, tr, state, _ = _make_state(tmp_path, task_manager=mgr)
    capsys.readouterr()
    _slash("/task output nope", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "unknown task" in out.lower()


# --- /cron ---------------------------------------------------------------


def test_slash_cron_without_registry_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=None)
    capsys.readouterr()
    assert _slash("/cron list", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "cron registry not configured" in out


def test_slash_cron_list_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "cron.json"
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=registry)
    capsys.readouterr()
    _slash("/cron list", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "no cron jobs" in out.lower()


def test_slash_cron_list_shows_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "cron.json"
    upsert_cron_job(registry, CronJob(name="doc-garden", schedule="0 6 * * *"))
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=registry)
    capsys.readouterr()
    _slash("/cron list", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "doc-garden" in out
    assert "0 6 * * *" in out
    assert "enabled" in out.lower()


def test_slash_cron_show_renders_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "cron.json"
    upsert_cron_job(
        registry,
        CronJob(
            name="doc-garden",
            schedule="0 6 * * *",
            description="garden the docs",
            tier_required="repo-write",
        ),
    )
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=registry)
    capsys.readouterr()
    _slash("/cron show doc-garden", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "doc-garden" in out
    assert "garden the docs" in out
    assert "repo-write" in out


def test_slash_cron_show_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "cron.json"
    upsert_cron_job(registry, CronJob(name="doc-garden", schedule="0 6 * * *"))
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=registry)
    capsys.readouterr()
    _slash("/cron show nope", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "unknown" in out.lower()


def test_slash_cron_toggle_disables_then_reenables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dream.tasks import get_cron_job

    registry = tmp_path / "cron.json"
    upsert_cron_job(registry, CronJob(name="doc-garden", schedule="0 6 * * *"))
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=registry)
    capsys.readouterr()
    _slash("/cron toggle doc-garden", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "disabled" in out.lower()
    job = get_cron_job(registry, "doc-garden")
    assert job is not None and job.enabled is False
    _slash("/cron toggle doc-garden", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "enabled" in out.lower()
    job = get_cron_job(registry, "doc-garden")
    assert job is not None and job.enabled is True


def test_slash_cron_toggle_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "cron.json"
    upsert_cron_job(registry, CronJob(name="doc-garden", schedule="0 6 * * *"))
    disp, tr, state, _ = _make_state(tmp_path, cron_registry_path=registry)
    capsys.readouterr()
    _slash("/cron toggle nope", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "unknown" in out.lower()


# --- /plan ---------------------------------------------------------------


def _write_plan(plans_root: Path, *, task_id: str, state_dir: str = "active") -> None:
    from datetime import UTC, datetime

    sections = {
        "Goal": "g",
        "Why now": "w",
        "Scope": "s",
        "Approach": "a",
        "Risks & mitigations": "r",
        "Definition of done": "d",
    }
    now = datetime.now(UTC)
    ledger = Ledger(
        task_id=task_id,
        state=state_dir,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        entries=(LedgerEntry(id="e1", description="first"),),
    )
    plan = ExecPlan(task_id=task_id, sections=sections, ledger=ledger)
    target = plan_dir(plans_root, state=state_dir)  # type: ignore[arg-type]
    target.mkdir(parents=True, exist_ok=True)
    write_plan(target, plan)


def test_slash_plan_without_root_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disp, tr, state, _ = _make_state(tmp_path, plans_root=None)
    capsys.readouterr()
    assert _slash("/plan list", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "plan store not configured" in out


def test_slash_plan_list_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans = tmp_path / "plans"
    disp, tr, state, _ = _make_state(tmp_path, plans_root=plans)
    capsys.readouterr()
    _slash("/plan list", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "no plans" in out.lower()


def test_slash_plan_list_groups_by_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans = tmp_path / "plans"
    _write_plan(plans, task_id="plan-001", state_dir="active")
    _write_plan(plans, task_id="plan-002", state_dir="draft")
    disp, tr, state, _ = _make_state(tmp_path, plans_root=plans)
    capsys.readouterr()
    _slash("/plan list", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "active" in out
    assert "draft" in out
    assert "plan-001" in out
    assert "plan-002" in out


def test_slash_plan_show_prints_sections_and_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans = tmp_path / "plans"
    _write_plan(plans, task_id="plan-001", state_dir="active")
    disp, tr, state, _ = _make_state(tmp_path, plans_root=plans)
    capsys.readouterr()
    _slash("/plan show plan-001", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "plan-001" in out
    assert "Goal" in out
    assert "e1" in out


def test_slash_plan_show_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans = tmp_path / "plans"
    _write_plan(plans, task_id="plan-001", state_dir="active")
    disp, tr, state, _ = _make_state(tmp_path, plans_root=plans)
    capsys.readouterr()
    _slash("/plan show nope", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "unknown" in out.lower() or "not found" in out.lower()
