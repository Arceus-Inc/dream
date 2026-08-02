"""Per-harness ownership and idle delivery for background subagents."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from dream.subagents._projection import SubagentResult


class DelegationStatus(StrEnum):
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class DelegationHandle:
    delegation_id: str
    status: DelegationStatus
    subagent_names: tuple[str, ...]


@dataclass(frozen=True)
class DelegationCompletion:
    delegation_id: str
    status: DelegationStatus
    results: tuple[SubagentResult, ...]
    error: str | None = None

    def render(self) -> str:
        lines = [f"[ASYNC DELEGATION COMPLETE — {self.delegation_id}]"]
        if self.error is not None:
            lines.append(f"status={self.status.value}; error={self.error}")
        for result in self.results:
            state = "completed" if result.success else "failed"
            detail = result.output if result.success else result.error or "unknown error"
            lines.append(f"- {result.name}: {state}\n{detail}")
        lines.append("Use these results in the work already in progress.")
        return "\n".join(lines)


_DelegationWork = Callable[[], Awaitable[tuple[SubagentResult, ...]]]


@dataclass(frozen=True)
class _ActiveDelegation:
    session_id: str
    task: asyncio.Task[None]


class AsyncDelegationManager:
    """Own background work and queue its completion for the parent session."""

    def __init__(self, *, max_active: int = 3, timeout_seconds: float = 300.0) -> None:
        if max_active < 1:
            raise ValueError("max_active must be >= 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._max_active = max_active
        self._timeout_seconds = timeout_seconds
        self._active: dict[str, _ActiveDelegation] = {}
        self._completed: defaultdict[str, deque[DelegationCompletion]] = defaultdict(deque)
        self._ready: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)

    def start(
        self,
        session_id: str,
        subagent_names: tuple[str, ...],
        work: _DelegationWork,
    ) -> DelegationHandle | None:
        if len(self._active) >= self._max_active:
            return None
        delegation_id = uuid4().hex[:12]
        task = asyncio.create_task(self._run(delegation_id, session_id, work))
        self._active[delegation_id] = _ActiveDelegation(session_id=session_id, task=task)
        return DelegationHandle(
            delegation_id=delegation_id,
            status=DelegationStatus.DISPATCHED,
            subagent_names=subagent_names,
        )

    def active(self, session_id: str) -> int:
        return sum(item.session_id == session_id for item in self._active.values())

    def drain(self, session_id: str) -> tuple[DelegationCompletion, ...]:
        queue = self._completed[session_id]
        items = tuple(queue)
        queue.clear()
        return items

    async def wait_next(self, session_id: str) -> DelegationCompletion:
        ready = self._ready[session_id]
        while True:
            ready.clear()
            queue = self._completed[session_id]
            if queue:
                return queue.popleft()
            if self.active(session_id) == 0:
                raise RuntimeError("session has no active background delegation")
            await ready.wait()

    async def cancel_session(self, session_id: str) -> None:
        tasks = [
            item.task for item in self._active.values() if item.session_id == session_id
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._completed.pop(session_id, None)
        self._ready.pop(session_id, None)

    async def close(self) -> None:
        tasks = [item.task for item in self._active.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._completed.clear()
        self._ready.clear()

    async def _run(
        self,
        delegation_id: str,
        session_id: str,
        work: _DelegationWork,
    ) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                results = await work()
            completion = DelegationCompletion(
                delegation_id=delegation_id,
                status=(
                    DelegationStatus.COMPLETED
                    if all(result.success for result in results)
                    else DelegationStatus.FAILED
                ),
                results=results,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            completion = DelegationCompletion(
                delegation_id=delegation_id,
                status=DelegationStatus.TIMED_OUT,
                results=(),
                error=f"background delegation exceeded {self._timeout_seconds:g}s",
            )
        except Exception as exc:  # the parent receives failure; the task never leaks it
            completion = DelegationCompletion(
                delegation_id=delegation_id,
                status=DelegationStatus.FAILED,
                results=(),
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._active.pop(delegation_id, None)
            self._ready[session_id].set()
        self._completed[session_id].append(completion)
        self._ready[session_id].set()


__all__ = [
    "AsyncDelegationManager",
    "DelegationCompletion",
    "DelegationHandle",
    "DelegationStatus",
]
