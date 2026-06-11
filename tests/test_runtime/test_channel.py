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
async def test_wake_command_uses_prompt_override(tmp_path: Path) -> None:
    # The persona's heartbeat prompt must drive MANUAL wakes too, not just
    # the idle scheduler — both paths read RuntimeConfig.wake_prompt_path.
    from dream.runtime import _runtime as runtime_mod
    from dream.wake import WakeOutcome

    seen: list[Any] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        seen.append(kwargs.get("prompt_override_path"))
        return WakeOutcome(decision=None, dropped_reason="test")

    harness = _harness(tmp_path, wake_streamer_factory=lambda: object())
    override = tmp_path / "heartbeat.md"
    config = RuntimeConfig(channel_poll_seconds=0.02, wake_prompt_path=override)
    rt = Runtime(harness, config)
    original = runtime_mod.run_wake_cycle
    runtime_mod.run_wake_cycle = fake_cycle  # type: ignore[assignment]
    try:
        async with rt:
            wake = WakeCommand()
            CommandInbox(rt.inbox_path).submit(wake)
            await _ack_for(rt, wake.id)
    finally:
        runtime_mod.run_wake_cycle = original  # type: ignore[assignment]
    assert seen == [override]


@pytest.mark.asyncio
async def test_wake_command_run_decision_invokes_handler(tmp_path: Path) -> None:
    # A manual wake whose heartbeat decides `run` must execute the decided
    # tasks through the wake_run_handler, exactly like a scheduled wake.
    from datetime import UTC, datetime

    from dream.runtime import _runtime as runtime_mod
    from dream.wake import HeartbeatDecision, ManualWake, WakeOutcome

    decision = HeartbeatDecision(
        decided_at=datetime.now(UTC),
        action="run",
        tasks=("brief the queued paper",),
        reason="queue has work",
        wake_source=ManualWake(),
        forced=False,
        outcome="decided",
    )

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        return WakeOutcome(decision=decision)

    handled: list[HeartbeatDecision] = []
    done = asyncio.Event()

    async def handler(d: HeartbeatDecision) -> None:
        handled.append(d)
        done.set()

    harness = _harness(tmp_path, wake_streamer_factory=lambda: object())
    rt = Runtime(
        harness,
        RuntimeConfig(channel_poll_seconds=0.02),
        wake_run_handler=handler,
    )
    original = runtime_mod.run_wake_cycle
    runtime_mod.run_wake_cycle = fake_cycle  # type: ignore[assignment]
    try:
        async with rt:
            wake = WakeCommand()
            CommandInbox(rt.inbox_path).submit(wake)
            ack = await _ack_for(rt, wake.id)
            assert ack.status == "ok"
            assert "1 task" in ack.summary
            await asyncio.wait_for(done.wait(), timeout=5)
    finally:
        runtime_mod.run_wake_cycle = original  # type: ignore[assignment]
    assert [d.tasks for d in handled] == [("brief the queued paper",)]


@pytest.mark.asyncio
async def test_wake_rejected_without_streamer(tmp_path: Path) -> None:
    async with Runtime(_harness(tmp_path), _FAST) as rt:
        wake = WakeCommand()
        CommandInbox(rt.inbox_path).submit(wake)
        ack = await _ack_for(rt, wake.id)
    assert ack.status == "rejected"
    assert "wake" in ack.summary.lower()


@pytest.mark.asyncio
async def test_job_wall_clock_budget_enforced(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    async def slow_run_task(**kwargs: Any) -> str:
        await asyncio.sleep(60)
        return "never"

    harness.run_task = slow_run_task  # type: ignore[method-assign]
    config = RuntimeConfig(channel_poll_seconds=0.02, job_timeout_seconds=0.1)
    async with Runtime(harness, config) as rt:
        command = SubmitTaskCommand(intent="slow", task_id="t-slow")
        CommandInbox(rt.inbox_path).submit(command)
        await _ack_for(rt, command.id)
        for _ in range(200):
            if "runtime.job.failed" in _event_types(rt.events_path):
                break
            await asyncio.sleep(0.02)
    events = [
        json.loads(line)
        for line in rt.events_path.read_text(encoding="utf-8").splitlines()
    ]
    failed = [e for e in events if e["type"] == "runtime.job.failed"]
    assert failed and "budget" in failed[0]["error"]


@pytest.mark.asyncio
async def test_failed_job_retried_then_succeeds(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    attempts = 0

    async def flaky_run_task(**kwargs: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return "done"

    harness.run_task = flaky_run_task  # type: ignore[method-assign]
    config = RuntimeConfig(channel_poll_seconds=0.02, job_max_retries=1)
    async with Runtime(harness, config) as rt:
        command = SubmitTaskCommand(intent="flaky", task_id="t-flaky")
        CommandInbox(rt.inbox_path).submit(command)
        await _ack_for(rt, command.id)
        for _ in range(200):
            if "runtime.job.finished" in _event_types(rt.events_path):
                break
            await asyncio.sleep(0.02)
    types = _event_types(rt.events_path)
    assert "runtime.job.retry" in types
    assert "runtime.job.finished" in types
    assert attempts == 2


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
