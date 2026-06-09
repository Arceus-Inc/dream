"""In-process teammate executor — runs a parent-supplied async factory
as an ``asyncio.Task`` and delivers its result through the file mailbox.

Spec criteria #21-#23: worker results reach the leader via a
``task_notification`` file in ``.harness/swarm/{leader}/inbox/``, never
through a synchronous return value or an in-process queue. (The lint
``test_swarm_does_not_use_in_memory_queues_for_messaging`` enforces this:
``asyncio.create_task`` is fine; ``asyncio.Queue`` for inter-agent comms
is not.)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from dream.swarm._identity import TeammateIdentity
from dream.swarm._mailbox import Mailbox, make_task_notification
from dream.swarm._paths import leader_inbox_dir, validate_leader_id
from dream.swarm._spawn import (
    MAX_SUBAGENT_DEPTH,
    BackendType,
    SpawnResult,
    TeammateSpawnConfig,
)

__all__ = [
    "EventSink",
    "InProcessExecutor",
    "InProcessFactory",
]

EventSink = Callable[[dict[str, Any]], None]
InProcessFactory = Callable[[TeammateSpawnConfig], Awaitable[str]]


def _depth_event(*, depth: int, agent_id: str) -> dict[str, Any]:
    return {
        "type": "subagent.depth_exceeded",
        "level": "info",
        "depth": depth,
        "max_depth": MAX_SUBAGENT_DEPTH,
        "agent_id": agent_id,
    }


@dataclass
class InProcessExecutor:
    """Spawn each teammate as an ``asyncio.Task`` in the parent loop.

    The factory is invoked with the :class:`TeammateSpawnConfig`; its
    awaited return value is the worker's summary string. Whether the
    factory returns normally or raises, the executor writes one
    ``task_notification`` to the leader's inbox.
    """

    worktree_root: Path
    leader_id: str
    factory: InProcessFactory
    event_sink: EventSink | None = None
    type: BackendType = field(default="in_process", init=False)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        validate_leader_id(self.leader_id)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(event)

    async def spawn(self, config: TeammateSpawnConfig) -> SpawnResult:
        agent_id = TeammateIdentity.create(
            name=config.name, team=config.team
        ).agent_id

        if config.depth > MAX_SUBAGENT_DEPTH:
            self._emit(_depth_event(depth=config.depth, agent_id=agent_id))
            return SpawnResult(
                task_id="",
                agent_id=agent_id,
                backend_type="in_process",
                success=False,
                error=(
                    f"exceeded subagent depth: depth={config.depth} > "
                    f"max={MAX_SUBAGENT_DEPTH}"
                ),
            )

        task_id = f"in_process_teammate-{uuid4().hex[:8]}"
        inbox = leader_inbox_dir(self.worktree_root, self.leader_id)
        mailbox = Mailbox(inbox)

        async def _runner() -> None:
            status = "completed"
            summary = ""
            try:
                result = await self.factory(config)
                summary = result if isinstance(result, str) else str(result)
            except BaseException as exc:
                status = "failed"
                summary = f"{type(exc).__name__}: {exc}"
            finally:
                msg = make_task_notification(
                    sender=agent_id,
                    recipient=self.leader_id,
                    task_id=task_id,
                    status=status,
                    summary=summary,
                )
                mailbox.write(msg)

        task = asyncio.create_task(_runner(), name=task_id)
        self._tasks[agent_id] = task
        # Evict the finished runner so a long-lived leader spawning many
        # teammates does not retain completed/cancelled task objects forever.
        # Guard against clobbering a re-spawn under the same agent_id: only
        # drop the entry if it still points at *this* task.
        task.add_done_callback(
            lambda done: self._evict_if_current(agent_id, done)
        )
        return SpawnResult(
            task_id=task_id, agent_id=agent_id, backend_type="in_process"
        )

    def _evict_if_current(self, agent_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(agent_id) is task:
            del self._tasks[agent_id]

    async def shutdown(self, agent_id: str, *, force: bool = False) -> bool:
        task = self._tasks.pop(agent_id, None)
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        return True

    async def wait_all(self) -> None:
        """Test helper — await every still-running runner task."""
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
