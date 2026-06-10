"""``Runtime`` — the long-running construct itself (spec 15 P1 §1).

``async with Runtime(harness) as rt: await rt.run_forever()`` owns what the
REPL used to own: boot gates, single-instance lock, event sink, supervised
cron/wake loops, task lifecycle listeners, graceful drain.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from dream import Harness, HarnessConfig
from dream.config.paths import DreamPaths
from dream.runtime import (
    Runtime,
    RuntimeBootBlockedError,
    RuntimeBusyError,
    RuntimeConfig,
)
from dream.state.sidecar import create_sidecar
from dream.tasks import BackgroundTaskManager


def _harness(tmp_path: Path, **config_kwargs: Any) -> Harness:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    paths = DreamPaths.resolve(repo, home=tmp_path / "home")
    config_kwargs.setdefault("paths", paths)
    return Harness(HarnessConfig(working_dir=repo, **config_kwargs))


def _events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _event_types(events_path: Path) -> list[str]:
    return [e["type"] for e in _events(events_path)]


@pytest.mark.asyncio
async def test_start_and_shutdown_emit_lifecycle_events(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    rt = Runtime(harness)
    async with rt:
        assert rt.events_path.exists()
    types = _event_types(rt.events_path)
    assert "runtime.started" in types
    assert "runtime.stopped" in types


@pytest.mark.asyncio
async def test_second_instance_is_refused(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    async with Runtime(harness):
        with pytest.raises(RuntimeBusyError):
            async with Runtime(_harness(tmp_path)):
                pass


@pytest.mark.asyncio
async def test_lock_released_after_shutdown(tmp_path: Path) -> None:
    async with Runtime(_harness(tmp_path)):
        pass
    # A fresh runtime can start once the first released the lock.
    async with Runtime(_harness(tmp_path)):
        pass


@pytest.mark.asyncio
async def test_boot_blocked_on_threat_finding(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    repo = harness.config.working_dir
    # A worktree secret triggers the spec-13E threat scan block.
    fake_aws = "AKIA" + "ABCDEFGHIJKLMNOP"
    (repo / ".env").write_text(f"AWS={fake_aws}\n", encoding="utf-8")
    rt = Runtime(harness)
    with pytest.raises(RuntimeBootBlockedError) as err:
        await rt.start()
    assert err.value.report.blocked
    # The lock must have been released on the failed boot: with the threat
    # removed, a fresh runtime can start.
    (repo / ".env").unlink()
    async with Runtime(_harness(tmp_path)):
        pass


@pytest.mark.asyncio
async def test_resume_candidates_surfaced_at_boot(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    paths = harness.config.paths
    assert paths is not None
    create_sidecar(paths, "t-inflight", base_branch="main", harness_version="0.1.0")

    rt = Runtime(harness)
    async with rt:
        report = rt.boot_report
        assert report is not None
        assert [c.task_id for c in report.resume_candidates] == ["t-inflight"]
    events = _events(rt.events_path)
    resumes = [e for e in events if e["type"] == "runtime.resume.candidate"]
    assert [e["task_id"] for e in resumes] == ["t-inflight"]


@pytest.mark.asyncio
async def test_cron_loop_supervised_when_registry_configured(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path / "repo", home=tmp_path / "home")
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    registry = tmp_path / "repo" / ".dream" / "cron" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text("[]", encoding="utf-8")
    harness = _harness(
        tmp_path, task_manager=manager, cron_registry_path=registry, paths=paths
    )
    rt = Runtime(harness)
    async with rt:
        assert "cron" in rt.running_loops
    assert "cron" not in rt.running_loops


@pytest.mark.asyncio
async def test_default_loops_without_subsystems(tmp_path: Path) -> None:
    # No task manager → no cron; no wake streamer → no wake. The command
    # channel (how the runtime is steered) and the liveness watchdog (the
    # board may appear once a swarm runs) are always on.
    rt = Runtime(_harness(tmp_path))
    async with rt:
        assert set(rt.running_loops) == {"watchdog", "channel"}


@pytest.mark.asyncio
async def test_task_lifecycle_mirrored_to_events(tmp_path: Path) -> None:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    harness = _harness(tmp_path, task_manager=manager)
    rt = Runtime(harness)
    async with rt:
        record = await manager.create_shell_task(
            description="say hi", cwd=tmp_path, command="echo hi"
        )
        for _ in range(100):
            current = manager.get_task(record.id)
            if current is not None and current.status == "completed":
                break
            await asyncio.sleep(0.05)
    types = _event_types(rt.events_path)
    assert "runtime.task.started" in types
    assert "runtime.task.finished" in types


@pytest.mark.asyncio
async def test_paths_override_drives_lock_and_gates(tmp_path: Path) -> None:
    # A frontend (the REPL) resolves DreamPaths from its own working dir +
    # env; the runtime must honour that override rather than re-deriving
    # from the harness config (whose working_dir may be the process cwd).
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    paths = DreamPaths.resolve(elsewhere, home=tmp_path / "home")
    harness = Harness(HarnessConfig(working_dir=elsewhere))
    rt = Runtime(harness, paths=paths)
    async with rt:
        assert (paths.dream_dir / "runtime.lock").exists()
        assert rt.events_path == paths.dream_dir / "runtime" / "events.jsonl"


@pytest.mark.asyncio
async def test_precomputed_boot_report_is_trusted(tmp_path: Path) -> None:
    # The REPL runs the gates itself (it needs the verdict before building
    # the skill registry); the runtime must not run them a second time.
    from dream.runtime import run_boot_gates

    harness = _harness(tmp_path)
    paths = harness.config.paths
    assert paths is not None
    report = run_boot_gates(working_dir=harness.config.working_dir, paths=paths)
    # Plant a threat AFTER the precomputed gates ran: a re-run would block.
    fake_aws = "AKIA" + "ABCDEFGHIJKLMNOP"
    (harness.config.working_dir / ".env").write_text(
        f"AWS={fake_aws}\n", encoding="utf-8"
    )
    async with Runtime(harness, boot_report=report) as rt:
        assert rt.boot_report is report


@pytest.mark.asyncio
async def test_run_forever_returns_on_request_stop(tmp_path: Path) -> None:
    rt = Runtime(_harness(tmp_path))
    async with rt:
        runner = asyncio.create_task(rt.run_forever())
        await asyncio.sleep(0)
        assert not runner.done()
        rt.request_stop()
        await asyncio.wait_for(runner, timeout=2)


@pytest.mark.asyncio
async def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    rt = Runtime(_harness(tmp_path))
    await rt.start()
    await rt.shutdown()
    await rt.shutdown()
    types = _event_types(rt.events_path)
    assert types.count("runtime.stopped") == 1


@pytest.mark.asyncio
async def test_drain_stops_running_tasks_after_timeout(tmp_path: Path) -> None:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    harness = _harness(tmp_path, task_manager=manager)
    rt = Runtime(harness, RuntimeConfig(drain_timeout_seconds=0.2))
    async with rt:
        record = await manager.create_shell_task(
            description="sleep long", cwd=tmp_path, command="sleep 60"
        )
        for _ in range(100):
            current = manager.get_task(record.id)
            if current is not None and current.status == "running":
                break
            await asyncio.sleep(0.05)
    stopped = manager.get_task(record.id)
    assert stopped is not None and stopped.status == "killed"
    types = _event_types(rt.events_path)
    assert "runtime.drain.stopped_task" in types
