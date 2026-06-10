"""Runtime-supervised swarm workers (spec 15 P5).

A teammate spawned through a :class:`~dream.swarm._spawn.TeammateExecutor`
becomes a *supervised child*: the supervisor watches the backing task to
a terminal state, restarts failures with linear backoff up to a cap, and
abandons loudly — the same bounded-everything discipline as the
runtime's own loops. The :class:`~dream.swarm.TeamRegistry` records what
the runtime currently hosts (membership is cleaned up on exit), and the
remote seam stays gated: a bridge refusal surfaces as a spawn failure,
never a crash.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dream.runtime._supervisor import EmitFn
from dream.swarm import TeamMember, TeamRegistry
from dream.swarm._spawn import SpawnResult, TeammateExecutor, TeammateSpawnConfig
from dream.tasks import BackgroundTaskManager, TaskRecord

__all__ = ["WorkerSupervisor"]

_TERMINAL = frozenset({"completed", "failed", "killed"})


@dataclass
class WorkerSupervisor:
    """Spawn → watch → restart-or-abandon for one executor's teammates."""

    executor: TeammateExecutor
    task_manager: BackgroundTaskManager
    emit: EmitFn
    registry: TeamRegistry | None = None
    max_restarts: int = 3
    backoff_seconds: float = 1.0
    poll_seconds: float = 0.25
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def run_worker(self, config: TeammateSpawnConfig) -> None:
        """Supervise one teammate to completion or abandonment."""
        crashes = 0
        while True:
            result = await self.executor.spawn(config)
            if not result.success:
                self.emit(
                    "runtime.worker.spawn_failed",
                    name=config.name,
                    team=config.team,
                    error=result.error,
                )
                crashes += 1
                if crashes > self.max_restarts:
                    self._emit_abandoned(config, crashes)
                    return
                await self.sleep(self.backoff_seconds * crashes)
                continue
            self.emit(
                "runtime.worker.started",
                agent_id=result.agent_id,
                task_id=result.task_id,
                team=config.team,
                restarts=crashes,
            )
            self._register(result, config)
            try:
                record = await self._wait_terminal(result.task_id)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await self.executor.shutdown(result.agent_id, force=True)
                self._deregister(config.team, result.agent_id)
                self.emit("runtime.worker.cancelled", agent_id=result.agent_id)
                raise
            self._deregister(config.team, result.agent_id)
            if record.status == "completed" and (record.return_code or 0) == 0:
                self.emit(
                    "runtime.worker.finished",
                    agent_id=result.agent_id,
                    task_id=result.task_id,
                )
                return
            crashes += 1
            self.emit(
                "runtime.worker.exited",
                agent_id=result.agent_id,
                status=record.status,
                return_code=record.return_code,
                restarts=crashes,
            )
            if crashes > self.max_restarts:
                self._emit_abandoned(config, crashes)
                return
            await self.sleep(self.backoff_seconds * crashes)

    async def _wait_terminal(self, task_id: str) -> TaskRecord:
        """Poll the task manager until the worker's task reaches a terminal state.

        Polling (not a completion listener) closes the fast-exit race: the
        record is already terminal by the time we first look — a listener
        registered now would never fire.
        """
        while True:
            record = self.task_manager.get_task(task_id)
            if record is not None and record.status in _TERMINAL:
                return record
            await self.sleep(self.poll_seconds)

    def _register(self, result: SpawnResult, config: TeammateSpawnConfig) -> None:
        if self.registry is None:
            return
        if config.team not in self.registry.list_teams():
            self.registry.create_team(name=config.team)
        self.registry.add_member(
            config.team,
            TeamMember(
                agent_id=result.agent_id,
                name=config.name,
                team=config.team,
                backend_type=self.executor.type,
                joined_at=time.time(),
                prompt=config.prompt,
                cwd=config.cwd,
                worktree_path=config.worktree_path,
                permissions=list(config.permissions),
            ),
        )

    def _deregister(self, team: str, agent_id: str) -> None:
        if self.registry is None:
            return
        with contextlib.suppress(Exception):
            self.registry.remove_member(team, agent_id)

    def _emit_abandoned(self, config: TeammateSpawnConfig, crashes: int) -> None:
        self.emit(
            "runtime.worker.abandoned",
            name=config.name,
            team=config.team,
            restarts=crashes,
        )
