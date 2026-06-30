"""Spec 07 slice 2 — :class:`BackgroundTaskManager`.

Ephemeral runtime layer beneath the durable exec-plan (slice 1). Spawns
shell-string or argv subprocesses, watches them to terminal state,
streams stdout/stderr to a per-task ``output_file``, and fires registered
**completion listeners** when a task reaches ``completed``/``failed``/
``killed``. Listeners are the seam to the durable layer — see
:mod:`dream.tasks._seam`.

Borrowed from OpenHarness ``src/openharness/tasks/manager.py`` (kept
the supervision shape and ``_generations`` counter; dropped interactive
``write_to_task``/auto-restart-on-BrokenPipe machinery and the teammate
spawner — those land in later slices).

Design choices that diverge from OpenHarness:

- ``TaskRecord`` is frozen; transitions go through ``with_*`` helpers and
  the manager rebinds its ``id -> TaskRecord`` map (rest of Dream is
  immutable-by-default).
- The output file lives under a caller-supplied ``tasks_dir`` (slice 2 is
  ephemeral but the on-disk log isn't — keep it under the test's tmp_path
  so we don't litter the repo).
- No durable on-disk state for the manager itself; all task records are
  in-memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from dream.tasks._types import TaskRecord, TaskStatus, TaskType
from dream.utils.fs import atomic_write_text

__all__ = [
    "AGENT_TASK_TYPES",
    "RESTART_NOTICE",
    "TERMINAL_STATUSES",
    "BackgroundTaskManager",
    "CompletionListener",
    "StartListener",
    "register_one_shot_completion",
]

TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {"completed", "failed", "killed"}
)
"""Task statuses that represent a finished task (natural exit or kill)."""

CompletionListener = Callable[[TaskRecord], Awaitable[None] | None]
"""Called with the terminal :class:`TaskRecord` after natural exit or
``stop_task``. Sync or async are both fine."""

StartListener = Callable[[TaskRecord], Awaitable[None] | None]
"""Called with the freshly-spawned :class:`TaskRecord` after its subprocess
starts. Sync or async; exceptions are swallowed by the manager."""

AGENT_TASK_TYPES: frozenset[TaskType] = frozenset(
    {"local_agent", "remote_agent", "in_process_teammate"}
)
"""Task types that may be restarted (agents resume; bash one-shots do not)."""

RESTART_NOTICE = (
    "[dream] Agent task restarted; prior interactive context was not preserved.\n"
)


def register_one_shot_completion(
    manager: BackgroundTaskManager,
    task_id: str,
    fn: Callable[[TaskRecord], None],
) -> None:
    """Run ``fn`` exactly once when ``task_id`` reaches a terminal state.

    A completion listener keyed to ``task_id`` fires ``fn`` on the first
    terminal transition, then unregisters itself so the listener set doesn't
    grow across many spawns. The spawn/register race is closed: if the task
    already reached a terminal state before this listener attached, ``fn`` is
    stamped from its current record immediately.

    ``fn`` is a *synchronous* one-shot stamp (the run-record / outcome write);
    the race-close path fires it inline, so an awaitable could not be awaited
    here. Every caller passes a sync listener.

    The caller must register *after* the task is spawned (so ``task_id`` is
    known); the race-close check makes a fast task that finished in between
    safe.
    """
    unregister: dict[str, Callable[[], None]] = {}
    fired = {"done": False}

    def _fire(task: TaskRecord) -> None:
        if fired["done"]:
            return
        fired["done"] = True
        try:
            fn(task)
        finally:
            unreg = unregister.get("fn")
            if unreg is not None:
                unreg()

    def _on_terminal(task: TaskRecord) -> None:
        if task.id == task_id and task.status in TERMINAL_STATUSES:
            _fire(task)

    unregister["fn"] = manager.register_completion_listener(_on_terminal)
    current = manager.get_task(task_id)
    if current is not None and current.status in TERMINAL_STATUSES:
        _fire(current)


@dataclass(frozen=True)
class _ListenerFailure:
    """Diagnostic record for a listener that raised during notification."""

    task_id: str
    error: str


def _task_id(task_type: TaskType) -> str:
    return f"{task_type}-{uuid4().hex[:8]}"


def _read_tail(path: Path, *, max_bytes: int) -> str:
    """Read the last ``max_bytes`` bytes of ``path`` without loading the whole file.

    Opens in binary and seeks from the end so memory is bounded by ``max_bytes``,
    not by the file size. The window may start mid-character; ``errors="replace"``
    on decode turns any partial leading byte sequence into a replacement char
    rather than raising, which is acceptable for a human-facing log tail.
    """
    if max_bytes <= 0:
        return ""
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        start = max(0, size - max_bytes)
        fh.seek(start)
        window = fh.read()
    return window.decode("utf-8", errors="replace")


class BackgroundTaskManager:
    """In-memory supervisor for background subprocess tasks."""

    def __init__(self, *, tasks_dir: Path) -> None:
        self._tasks_dir = Path(tasks_dir)
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, TaskRecord] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Task[None]] = {}
        self._output_locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._listeners: dict[str, CompletionListener] = {}
        self._start_listeners: dict[str, StartListener] = {}
        # Tasks the manager itself is tearing down (stop or restart). The
        # watcher checks this and skips its own listener notification so we
        # don't fire twice for the same terminal transition.
        self._suppress_watcher_notify: set[str] = set()
        # Bounded ring of recent listener failures for diagnostics.
        self._listener_failures: list[_ListenerFailure] = []
        self._max_listener_failures: int = 32

    # --- creation ---------------------------------------------------------

    async def create_shell_task(
        self,
        *,
        description: str,
        cwd: str | Path,
        command: str | None = None,
        argv: list[str] | None = None,
        task_type: TaskType = "local_bash",
        env: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> TaskRecord:
        """Spawn a background subprocess and return its initial record.

        Exactly one of ``command`` (shell-evaluated string) or ``argv`` (a
        direct exec vector) must be supplied. The argv form bypasses the
        shell entirely — preferred whenever the input string contains
        metacharacters or paths the shell would mis-quote.
        """
        if command is None and argv is None:
            raise ValueError("create_shell_task requires either command or argv")
        if command is not None and argv is not None:
            raise ValueError("create_shell_task accepts only one of command or argv")

        task_id = _task_id(task_type)
        output_path = self._tasks_dir / f"{task_id}.log"
        # Seed an empty file so ``read_task_output`` is race-free against
        # the first chunk arriving from the subprocess.
        atomic_write_text(output_path, "")
        now = time.time()
        record = TaskRecord(
            id=task_id,
            type=task_type,
            status="running",
            description=description,
            cwd=str(Path(cwd).resolve()),
            output_file=output_path,
            command=command,
            argv=tuple(argv) if argv is not None else None,
            created_at=now,
            started_at=now,
            env=dict(env) if env is not None else None,
            metadata=dict(metadata) if metadata is not None else {},
        )
        self._tasks[task_id] = record
        self._output_locks[task_id] = asyncio.Lock()
        try:
            await self._start_process(task_id)
        except BaseException:
            # Spawn failed (e.g. argv[0] not found): roll back so we don't
            # leave a ghost task stuck in ``running`` with no process behind
            # it. The manager state must only reflect successfully started
            # tasks.
            self._tasks.pop(task_id, None)
            self._output_locks.pop(task_id, None)
            self._generations.pop(task_id, None)
            self._processes.pop(task_id, None)
            self._waiters.pop(task_id, None)
            raise
        await self._notify_start_listeners(record)
        return record

    # --- lookup -----------------------------------------------------------

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def list_tasks(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def generation(self, task_id: str) -> int:
        if task_id not in self._tasks:
            raise ValueError(f"No task found with ID: {task_id}")
        return self._generations.get(task_id, 0)

    def read_task_output(self, task_id: str, *, max_bytes: int = 12000) -> str:
        """Return the last ``max_bytes`` bytes of the task's output log.

        Seeks from the end and reads a bounded window so memory stays O(max_bytes)
        regardless of total log size — a multi-MB log no longer loads in full just
        to surface a small tail.
        """
        task = self._require_task(task_id)
        return _read_tail(task.output_file, max_bytes=max_bytes)

    # --- control ----------------------------------------------------------

    async def stop_task(self, task_id: str) -> TaskRecord:
        """Terminate a running task; transitions it to ``killed`` and
        fires completion listeners."""
        task = self._require_task(task_id)
        process = self._processes.get(task_id)
        if process is None:
            if task.status in {"completed", "failed", "killed"}:
                return task
            raise ValueError(f"Task {task_id} is not running")

        # stop_task owns this terminal transition; tear the process down with
        # the watcher suppressed so it doesn't also fire listeners or race the
        # final record rebind below.
        await self._terminate_process(task_id, process)

        updated = task.with_status("killed").with_ended(time.time())
        if process.returncode is not None:
            updated = updated.with_return_code(process.returncode)
        self._tasks[task_id] = updated
        self._processes.pop(task_id, None)
        await self._notify_listeners(updated)
        return updated

    async def restart_task(self, task_id: str) -> TaskRecord:
        """Restart an agent-like task. Kills the current process (if any),
        bumps ``_generations``, writes a restart notice to ``output_file``,
        and spawns a fresh subprocess."""
        task = self._require_task(task_id)
        if task.type not in AGENT_TASK_TYPES:
            raise ValueError(
                f"task {task_id} of type {task.type!r} cannot be restarted"
            )
        if task.command is None and task.argv is None:
            raise ValueError(f"task {task_id} has no command/argv to restart")

        process = self._processes.get(task_id)
        if process is not None:
            await self._terminate_process(task_id, process)
            self._processes.pop(task_id, None)

        async with self._output_locks[task_id]:
            with task.output_file.open("a", encoding="utf-8") as fh:
                fh.write(RESTART_NOTICE)

        # Rebind to a fresh run: status back to ``running`` AND clear the
        # terminal fields from the prior generation. Leaving stale
        # ``ended_at``/``return_code`` (and a prior ``started_at``) would
        # make the restarted task look already-finished to observers.
        self._tasks[task_id] = replace(
            task,
            status="running",
            started_at=time.time(),
            ended_at=None,
            return_code=None,
        )
        await self._start_process(task_id)
        return self._tasks[task_id]

    # --- listeners --------------------------------------------------------

    def register_completion_listener(
        self, listener: CompletionListener
    ) -> Callable[[], None]:
        """Register a callback fired on every terminal transition. Returns
        an unregister callable."""
        listener_id = uuid4().hex
        self._listeners[listener_id] = listener

        def _unregister() -> None:
            self._listeners.pop(listener_id, None)

        return _unregister

    def register_start_listener(
        self, listener: StartListener
    ) -> Callable[[], None]:
        """Register a callback fired after every successful task spawn.

        Symmetric to :meth:`register_completion_listener`. Used by the REPL
        to surface cron-driven (and any other background) task starts
        alongside ordinary tool calls.
        """
        listener_id = uuid4().hex
        self._start_listeners[listener_id] = listener

        def _unregister() -> None:
            self._start_listeners.pop(listener_id, None)

        return _unregister

    # --- internals --------------------------------------------------------

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"No task found with ID: {task_id}")
        return task

    async def _terminate_process(
        self, task_id: str, process: asyncio.subprocess.Process
    ) -> None:
        """Tear down ``process`` (terminate→wait→timeout→kill→cancel_waiter).

        The watcher is suppressed for the duration so it doesn't fire listeners
        or race the caller's final record rebind — ``stop_task`` / ``restart_task``
        own the terminal transition. The suppression is cleared even if
        ``terminate()`` raises (e.g. ``ProcessLookupError`` when the child
        already exited), otherwise the task id is wedged in the suppress set
        forever and a later natural completion never notifies.
        """
        self._suppress_watcher_notify.add(task_id)
        try:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            await self._cancel_waiter(task_id)
        finally:
            self._suppress_watcher_notify.discard(task_id)

    async def _cancel_waiter(self, task_id: str) -> None:
        waiter = self._waiters.pop(task_id, None)
        if waiter is None or waiter.done():
            return
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await waiter

    async def _start_process(self, task_id: str) -> asyncio.subprocess.Process:
        task = self._require_task(task_id)
        generation = self._generations.get(task_id, 0) + 1
        self._generations[task_id] = generation

        merged_env: dict[str, str] | None = (
            {**os.environ, **task.env} if task.env else None
        )

        if task.argv is not None:
            process = await asyncio.create_subprocess_exec(
                *task.argv,
                cwd=task.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=merged_env,
            )
        else:
            assert task.command is not None
            process = await asyncio.create_subprocess_shell(
                task.command,
                cwd=task.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=merged_env,
            )

        self._processes[task_id] = process
        self._waiters[task_id] = asyncio.create_task(
            self._watch_process(task_id, process, generation)
        )
        return process

    async def _watch_process(
        self,
        task_id: str,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        reader = asyncio.create_task(self._copy_output(task_id, process))
        return_code = await process.wait()
        with contextlib.suppress(asyncio.CancelledError):
            await reader

        if self._generations.get(task_id) != generation:
            return

        task = self._tasks.get(task_id)
        if task is None:
            return
        updated = task.with_return_code(return_code).with_ended(time.time())
        if updated.status != "killed":
            updated = updated.with_status(
                "completed" if return_code == 0 else "failed"
            )
        self._tasks[task_id] = updated
        self._processes.pop(task_id, None)
        self._waiters.pop(task_id, None)
        if task_id in self._suppress_watcher_notify:
            return  # stop_task / restart_task owns the listener fire
        await self._notify_listeners(updated)

    async def _copy_output(
        self, task_id: str, process: asyncio.subprocess.Process
    ) -> None:
        if process.stdout is None:
            return
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                return
            async with self._output_locks[task_id]:
                with self._tasks[task_id].output_file.open("ab") as fh:
                    fh.write(chunk)

    async def _notify_listeners(self, task: TaskRecord) -> None:
        # One bad listener must not block the others or crash the supervisor.
        # Failures are recorded in ``_listener_failures`` for diagnostic
        # retrieval (typed event stream will supersede this — Spec 00 rule 4).
        for listener in list(self._listeners.values()):
            try:
                result = listener(task)
                # ``inspect.isawaitable`` covers coroutines AND other
                # awaitables (Futures, objects with ``__await__``) — not just
                # native coroutines like ``asyncio.iscoroutine`` does.
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                self._record_listener_failure(task.id, exc)

    async def _notify_start_listeners(self, task: TaskRecord) -> None:
        for listener in list(self._start_listeners.values()):
            try:
                result = listener(task)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                self._record_listener_failure(task.id, exc)

    def _record_listener_failure(self, task_id: str, exc: Exception) -> None:
        self._listener_failures.append(
            _ListenerFailure(task_id=task_id, error=repr(exc))
        )
        if len(self._listener_failures) > self._max_listener_failures:
            self._listener_failures = self._listener_failures[-self._max_listener_failures:]

    @property
    def listener_failures(self) -> list[_ListenerFailure]:
        """Recent listener failures, oldest-first (bounded to last 32)."""
        return list(self._listener_failures)
