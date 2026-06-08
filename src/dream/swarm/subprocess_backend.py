"""Subprocess teammate executor.

Spawns each teammate as a ``local_agent`` task via the shipped spec-07
:class:`BackgroundTaskManager`, then bridges that task's
completion-listener seam (decision #11) to the file mailbox bus: when the
worker process exits the listener writes one ``task_notification`` to the
leader's inbox.

The actual argv for ``dream.repl session --role …`` is the leader-loop's
concern (slice 10-G); here we accept a pluggable ``argv_builder`` so
tests can spawn a benign one-shot subprocess and so 10-G can later wire
the real CLI without changing this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dream.swarm._mailbox import Mailbox, make_task_notification
from dream.swarm._paths import leader_inbox_dir, validate_leader_id
from dream.swarm._spawn import (
    MAX_SUBAGENT_DEPTH,
    BackendType,
    SpawnResult,
    TeammateSpawnConfig,
)
from dream.swarm.in_process import EventSink, _depth_event
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._types import TaskRecord

__all__ = ["ArgvBuilder", "SubprocessExecutor"]

ArgvBuilder = Callable[[TeammateSpawnConfig], list[str]]


def _default_argv(config: TeammateSpawnConfig) -> list[str]:
    raise NotImplementedError(
        "SubprocessExecutor requires an explicit argv_builder until slice 10-G "
        f"wires the dream.repl session CLI (config={config.name}@{config.team})"
    )


@dataclass
class SubprocessExecutor:
    """``TeammateExecutor`` that runs each teammate as a child process."""

    worktree_root: Path
    leader_id: str
    task_manager: BackgroundTaskManager
    argv_builder: ArgvBuilder = field(default=_default_argv)
    event_sink: EventSink | None = None
    type: BackendType = field(default="subprocess", init=False)
    _agent_tasks: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        validate_leader_id(self.leader_id)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(event)

    async def spawn(self, config: TeammateSpawnConfig) -> SpawnResult:
        agent_id = f"{config.name}@{config.team}"

        if config.depth > MAX_SUBAGENT_DEPTH:
            self._emit(_depth_event(depth=config.depth, agent_id=agent_id))
            return SpawnResult(
                task_id="",
                agent_id=agent_id,
                backend_type="subprocess",
                success=False,
                error=(
                    f"exceeded subagent depth: depth={config.depth} > "
                    f"max={MAX_SUBAGENT_DEPTH}"
                ),
            )

        inbox = leader_inbox_dir(self.worktree_root, self.leader_id)
        mailbox = Mailbox(inbox)

        # Register the listener BEFORE create_shell_task so a very-fast
        # child exit cannot fire the watcher before we are wired up.
        # Filter by the task id we are about to create (captured by
        # closure once we have the record).
        captured_task_id: dict[str, str] = {}

        def _listener(record: TaskRecord) -> None:
            wanted = captured_task_id.get("id")
            if wanted is None or record.id != wanted:
                return
            status = (
                record.status
                if record.status in {"completed", "failed", "killed"}
                else "failed"
            )
            summary = f"subprocess {record.id} -> {record.status}"
            if record.return_code is not None:
                summary += f" (rc={record.return_code})"
            msg = make_task_notification(
                sender=agent_id,
                recipient=self.leader_id,
                task_id=record.id,
                status=status,
                summary=summary,
            )
            mailbox.write(msg)

        unregister = self.task_manager.register_completion_listener(_listener)

        try:
            argv = self.argv_builder(config)
            record = await self.task_manager.create_shell_task(
                description=f"Teammate: {agent_id}",
                cwd=config.cwd,
                argv=argv,
                task_type="local_agent",
            )
        except BaseException as exc:  # noqa: BLE001 — convert to result
            unregister()
            return SpawnResult(
                task_id="",
                agent_id=agent_id,
                backend_type="subprocess",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        captured_task_id["id"] = record.id
        self._agent_tasks[agent_id] = record.id
        return SpawnResult(
            task_id=record.id, agent_id=agent_id, backend_type="subprocess"
        )

    async def shutdown(self, agent_id: str, *, force: bool = False) -> bool:
        task_id = self._agent_tasks.pop(agent_id, None)
        if task_id is None:
            return False
        try:
            await self.task_manager.stop_task(task_id)
        except ValueError:
            return False
        return True
