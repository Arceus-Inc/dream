"""Runtime ↔ inbound channel integration (spec 15 P2).

With the runtime running, a command dropped in the inbox is drained,
handled, and acked on the event stream — submit starts a job, cancel
stops it, status reports, wake fires a cycle (or is rejected when wake
isn't configured).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from dream import Harness, HarnessConfig
from dream.channels import (
    CancelCommand,
    CommandInbox,
    StatusCommand,
    SubmitTaskCommand,
    WakeCommand,
    read_ack,
)
from dream.config.paths import DreamPaths
from dream.runtime import Runtime, RuntimeConfig

_FAST = RuntimeConfig(channel_poll_seconds=0.02)


def _harness(tmp_path: Path, **config_kwargs: Any) -> Harness:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    paths = DreamPaths.resolve(repo, home=tmp_path / "home")
    config_kwargs.setdefault("paths", paths)
    return Harness(HarnessConfig(working_dir=repo, **config_kwargs))


async def _ack_for(rt: Runtime, command_id: str, timeout: float = 5.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        ack = read_ack(rt.events_path, command_id=command_id)
        if ack is not None:
            return ack
        await asyncio.sleep(0.02)
    raise AssertionError(f"no ack for {command_id}")


def _event_types(events_path: Path) -> list[str]:
    return [
        json.loads(line)["type"]
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_channel_loop_runs_and_acks_status(tmp_path: Path) -> None:
    async with Runtime(_harness(tmp_path), _FAST) as rt:
        assert "channel" in rt.running_loops
        command = StatusCommand()
        CommandInbox(rt.inbox_path).submit(command)
        ack = await _ack_for(rt, command.id)
    assert ack.status == "ok"
    assert "loops" in ack.summary
    assert str(rt.events_path) in ack.artifacts


@pytest.mark.asyncio
async def test_submit_task_runs_job_and_emits_finished(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    ran: list[str] = []

    async def fake_run_task(**kwargs: Any) -> str:
        ran.append(kwargs["intent"])
        return "done"

    # The runtime calls harness.run_task; the fake keeps the test offline.
    harness.run_task = fake_run_task  # type: ignore[method-assign]
    async with Runtime(harness, _FAST) as rt:
        command = SubmitTaskCommand(intent="fix the build", max_sprints=2)
        CommandInbox(rt.inbox_path).submit(command)
        ack = await _ack_for(rt, command.id)
        assert ack.status == "ok"
        for _ in range(100):
            if "runtime.job.finished" in _event_types(rt.events_path):
                break
            await asyncio.sleep(0.02)
    assert ran == ["fix the build"]
    assert "runtime.job.finished" in _event_types(rt.events_path)


@pytest.mark.asyncio
async def test_submit_failure_emits_job_failed(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    async def broken_run_task(**kwargs: Any) -> str:
        raise RuntimeError("no engine")

    harness.run_task = broken_run_task  # type: ignore[method-assign]
    async with Runtime(harness, _FAST) as rt:
        command = SubmitTaskCommand(intent="anything")
        CommandInbox(rt.inbox_path).submit(command)
        await _ack_for(rt, command.id)
        for _ in range(100):
            if "runtime.job.failed" in _event_types(rt.events_path):
                break
            await asyncio.sleep(0.02)
    assert "runtime.job.failed" in _event_types(rt.events_path)


@pytest.mark.asyncio
async def test_cancel_running_job(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    started = asyncio.Event()

    async def hanging_run_task(**kwargs: Any) -> str:
        started.set()
        await asyncio.Event().wait()
        return "never"

    harness.run_task = hanging_run_task  # type: ignore[method-assign]
    async with Runtime(harness, _FAST) as rt:
        submit = SubmitTaskCommand(intent="hang", task_id="t-hang")
        CommandInbox(rt.inbox_path).submit(submit)
        await _ack_for(rt, submit.id)
        await asyncio.wait_for(started.wait(), timeout=5)

        cancel = CancelCommand(task_id="t-hang")
        CommandInbox(rt.inbox_path).submit(cancel)
        ack = await _ack_for(rt, cancel.id)
        assert ack.status == "ok"
    assert "runtime.job.cancelled" in _event_types(rt.events_path)


@pytest.mark.asyncio
async def test_cancel_unknown_task_rejected(tmp_path: Path) -> None:
    async with Runtime(_harness(tmp_path), _FAST) as rt:
        cancel = CancelCommand(task_id="t-nope")
        CommandInbox(rt.inbox_path).submit(cancel)
        ack = await _ack_for(rt, cancel.id)
    assert ack.status == "rejected"


@pytest.mark.asyncio
async def test_wake_rejected_without_streamer(tmp_path: Path) -> None:
    async with Runtime(_harness(tmp_path), _FAST) as rt:
        wake = WakeCommand()
        CommandInbox(rt.inbox_path).submit(wake)
        ack = await _ack_for(rt, wake.id)
    assert ack.status == "rejected"
    assert "wake" in ack.summary.lower()


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_jobs(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    started = asyncio.Event()

    async def hanging_run_task(**kwargs: Any) -> str:
        started.set()
        await asyncio.Event().wait()
        return "never"

    harness.run_task = hanging_run_task  # type: ignore[method-assign]
    rt = Runtime(harness, _FAST)
    async with rt:
        submit = SubmitTaskCommand(intent="hang", task_id="t-hang2")
        CommandInbox(rt.inbox_path).submit(submit)
        await asyncio.wait_for(started.wait(), timeout=5)
    assert "runtime.job.cancelled" in _event_types(rt.events_path)
