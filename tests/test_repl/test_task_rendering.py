"""Tests for the REPL's background-task lifecycle rendering.

Two new surfaces:

1. ``render_task_started`` / ``render_task_finished`` mirror the
   ``ToolUseStart`` shape (``▸ label  description``) so cron firings and
   ad-hoc ``task_create`` calls show up inline next to tool calls.
2. ``build_default_harness`` wires the ``BackgroundTaskManager`` onto the
   typed ``HarnessConfig.task_manager`` field so the REPL can subscribe to
   start + completion listeners.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from dream.repl._events import EventSink
from dream.repl._session import (
    _task_label,
    build_default_harness,
    render_task_finished,
    render_task_started,
)
from dream.tasks import BackgroundTaskManager
from dream.tasks._types import TaskRecord


def _make_task(
    *,
    task_id: str = "tsk_1",
    status: str = "running",
    metadata: dict[str, str] | None = None,
    started_at: float | None = None,
    ended_at: float | None = None,
    return_code: int | None = None,
    description: str = "echo hi",
) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        type="local_bash",
        status=status,  # type: ignore[arg-type]
        description=description,
        cwd="/tmp",
        output_file=Path("/tmp/out.log"),
        command="echo hi",
        metadata=metadata or {},
        started_at=started_at,
        ended_at=ended_at,
        return_code=return_code,
    )


# --- labels ----------------------------------------------------------------


def test_task_label_uses_cron_kind_when_metadata_present() -> None:
    task = _make_task(metadata={"cron.kind": "heartbeat", "cron.run_id": "run_42"})
    assert _task_label(task) == "cron:heartbeat run_42"


def test_task_label_falls_back_to_task_id_for_ad_hoc_spawns() -> None:
    task = _make_task(task_id="tsk_abc")
    assert _task_label(task) == "task tsk_abc"


# --- start renderer --------------------------------------------------------


def test_render_task_started_prints_label_and_description(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    task = _make_task(task_id="tsk_99", description="ls -la")
    render_task_started(task, sink=sink, output=out)
    text = out.getvalue()
    # The arrow + label + description must all be there so the user sees
    # at a glance that a background task fired.
    assert "\u25b8" in text
    assert "task tsk_99" in text
    assert "ls -la" in text
    payload = json.loads(
        (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert payload["type"] == "session.task_started"
    assert payload["task_id"] == "tsk_99"
    assert payload["task_type"] == "local_bash"


def test_render_task_started_uses_cron_label_for_cron_firings(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    task = _make_task(
        task_id="tsk_cron",
        metadata={"cron.kind": "heartbeat", "cron.run_id": "run_7"},
        description="heartbeat tick",
    )
    render_task_started(task, sink=sink, output=out)
    # The verbatim user request was: cron firings should show up in the
    # REPL "just like the tool calls or other things" — and they need to
    # be distinguishable from ad-hoc task_create calls.
    assert "cron:heartbeat run_7" in out.getvalue()


# --- finish renderer -------------------------------------------------------


def test_render_task_finished_completed_shows_rc_and_duration(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    task = _make_task(
        task_id="tsk_done",
        status="completed",
        started_at=1000.0,
        ended_at=1002.5,
        return_code=0,
    )
    render_task_finished(task, sink=sink, output=out)
    text = out.getvalue()
    assert "\u21b3" in text
    assert "task tsk_done" in text
    assert "completed" in text
    assert "rc=0" in text
    assert "2.5s" in text
    payload = json.loads(
        (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert payload["type"] == "session.task_finished"
    assert payload["status"] == "completed"
    assert payload["return_code"] == 0


def test_render_task_finished_failed_status_is_emitted(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    task = _make_task(status="failed", return_code=1)
    render_task_finished(task, sink=sink, output=out)
    assert "failed" in out.getvalue()
    assert "rc=1" in out.getvalue()


# --- harness wiring --------------------------------------------------------


def test_build_default_harness_wires_task_manager(tmp_path: Path) -> None:
    """The REPL subscribes via ``harness.config.task_manager`` — if the field
    stops being populated, lifecycle listeners silently never register and
    cron firings stop being visible.
    """
    env = {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_SMOKE_BASE_URL": "http://127.0.0.1:9/v1",
    }
    harness = build_default_harness(env=env, working_dir=tmp_path)
    tm = harness.config.task_manager
    assert isinstance(tm, BackgroundTaskManager)
