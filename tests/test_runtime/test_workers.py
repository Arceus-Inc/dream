"""Runtime-supervised swarm workers (spec 15 P5).

Subprocess teammates become supervised children: spawn → watch the
backing task to a terminal state → restart on failure with backoff up
to a cap → abandon loudly. The team registry records what the runtime
hosts; the remote seam stays gated (bridge refuses in v1).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from dream import Harness, HarnessConfig
from dream.config.paths import DreamPaths
from dream.runtime import Runtime, RuntimeConfig
from dream.runtime._workers import WorkerSupervisor
from dream.swarm import TeamRegistry
from dream.swarm._remote import RemoteExecutor
from dream.swarm._spawn import TeammateSpawnConfig
from dream.swarm.subprocess_backend import SubprocessExecutor
from dream.tasks import BackgroundTaskManager


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return payload

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


def _config(tmp_path: Path, name: str = "worker-a") -> TeammateSpawnConfig:
    return TeammateSpawnConfig(
        name=name,
        team="builders",
        prompt="do the thing",
        cwd=str(tmp_path),
        parent_session_id="s-leader",
    )


def _supervisor(
    tmp_path: Path,
    *,
    argv: list[str],
    emit: _Recorder,
    max_restarts: int = 1,
    registry: TeamRegistry | None = None,
) -> WorkerSupervisor:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    executor = SubprocessExecutor(
        worktree_root=tmp_path,
        leader_id="leader-1",
        task_manager=manager,
        argv_builder=lambda config: argv,
    )
    return WorkerSupervisor(
        executor=executor,
        task_manager=manager,
        emit=emit,
        registry=registry,
        max_restarts=max_restarts,
        backoff_seconds=0,
        poll_seconds=0.02,
    )


@pytest.mark.asyncio
async def test_clean_exit_finishes_without_restart(tmp_path: Path) -> None:
    emit = _Recorder()
    supervisor = _supervisor(tmp_path, argv=["true"], emit=emit)
    await asyncio.wait_for(supervisor.run_worker(_config(tmp_path)), timeout=15)
    types = emit.types()
    assert types.count("runtime.worker.started") == 1
    assert "runtime.worker.finished" in types
    assert "runtime.worker.abandoned" not in types


@pytest.mark.asyncio
async def test_failing_worker_restarts_then_abandons(tmp_path: Path) -> None:
    emit = _Recorder()
    supervisor = _supervisor(tmp_path, argv=["false"], emit=emit, max_restarts=1)
    await asyncio.wait_for(supervisor.run_worker(_config(tmp_path)), timeout=30)
    types = emit.types()
    assert types.count("runtime.worker.started") == 2  # initial + 1 restart
    assert types.count("runtime.worker.exited") == 2
    assert "runtime.worker.abandoned" in types
    assert "runtime.worker.finished" not in types


@pytest.mark.asyncio
async def test_registry_records_hosted_worker(tmp_path: Path) -> None:
    emit = _Recorder()
    registry = TeamRegistry(tmp_path)
    supervisor = _supervisor(tmp_path, argv=["true"], emit=emit, registry=registry)
    await asyncio.wait_for(supervisor.run_worker(_config(tmp_path)), timeout=15)
    # Worker came and went: team exists, membership cleaned up on exit.
    team = registry.get_team("builders")
    assert team.members == {}


@pytest.mark.asyncio
async def test_cancellation_stops_the_child(tmp_path: Path) -> None:
    emit = _Recorder()
    supervisor = _supervisor(tmp_path, argv=["sleep", "60"], emit=emit)
    task = asyncio.create_task(supervisor.run_worker(_config(tmp_path)))
    for _ in range(200):
        if "runtime.worker.started" in emit.types():
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "runtime.worker.cancelled" in emit.types()


@pytest.mark.asyncio
async def test_remote_seam_stays_gated(tmp_path: Path) -> None:
    # Spec 15 P5 §3: the bridge keeps refusing until a real remote backend
    # exists; the supervisor surfaces the refusal and abandons.
    emit = _Recorder()
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    supervisor = WorkerSupervisor(
        executor=RemoteExecutor(worktree_root=tmp_path, leader_id="leader-1"),
        task_manager=manager,
        emit=emit,
        max_restarts=0,
        backoff_seconds=0,
        poll_seconds=0.02,
    )
    await asyncio.wait_for(supervisor.run_worker(_config(tmp_path)), timeout=10)
    types = emit.types()
    assert "runtime.worker.spawn_failed" in types
    assert "runtime.worker.abandoned" in types
    failures = [p for t, p in emit.events if t == "runtime.worker.spawn_failed"]
    assert "bridge disabled" in failures[0]["error"]


@pytest.mark.asyncio
async def test_runtime_start_worker_end_to_end(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = DreamPaths.resolve(repo, home=tmp_path / "home")
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    harness = Harness(
        HarnessConfig(working_dir=repo, paths=paths, task_manager=manager)
    )
    executor = SubprocessExecutor(
        worktree_root=repo,
        leader_id="leader-1",
        task_manager=manager,
        argv_builder=lambda config: ["true"],
    )
    rt = Runtime(harness, RuntimeConfig(channel_poll_seconds=0.02))
    async with rt:
        worker = rt.start_worker(_config(repo), executor=executor)
        await asyncio.wait_for(worker, timeout=15)
    events = [
        json.loads(line)
        for line in rt.events_path.read_text(encoding="utf-8").splitlines()
    ]
    types = [e["type"] for e in events]
    assert "runtime.worker.started" in types
    assert "runtime.worker.finished" in types
