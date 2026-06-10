"""``Runtime`` — the long-running construct itself (spec 15 P1 §1).

Owns what the REPL used to own: single-instance lock, boot gates, the
event stream, supervised cron/wake loops, task lifecycle mirroring, and
graceful drain. Deterministic loops outside, LLM only inside turns.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.errors import DreamError
from dream.harness import Harness
from dream.observability import EventSink
from dream.runtime._boot import BootReport, run_boot_gates
from dream.runtime._supervisor import supervise_loop
from dream.runtime._wake_scheduler import wake_scheduler_loop
from dream.services.cron import cron_tick_loop
from dream.tasks import BackgroundTaskManager, TaskRecord
from dream.utils.file_lock import try_exclusive_file_lock
from dream.wake import HeartbeatConfig, HeartbeatDecision

__all__ = [
    "Runtime",
    "RuntimeBootBlockedError",
    "RuntimeBusyError",
    "RuntimeConfig",
]

_RUNTIME_LOCK_NAME = "runtime.lock"
_DRAIN_POLL_SECONDS = 0.05


class RuntimeBusyError(DreamError):
    """Another runtime instance already holds this repo's runtime lock."""

    code = "dream.runtime.busy"


class RuntimeBootBlockedError(DreamError):
    """A blocking boot gate (skills, threat scan) refused to start."""

    code = "dream.runtime.boot_blocked"

    def __init__(self, report: BootReport) -> None:
        messages = "; ".join(f.message for f in report.blocking_findings())
        super().__init__(f"runtime boot blocked: {messages}")
        self.report = report


@dataclass(frozen=True)
class RuntimeConfig:
    """Tunables for a :class:`Runtime`.

    ``wake_idle_minutes=None`` (the default) disables the wake scheduler;
    it also stays off when the harness has no ``wake_streamer_factory``.
    ``cron_poll_seconds=None`` defers to the cron service's default.
    """

    agent_id: str = "default"
    events_path: Path | None = None
    cron_poll_seconds: int | None = None
    wake_idle_minutes: int | None = None
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    drain_timeout_seconds: float = 10.0
    max_loop_restarts: int = 5


class Runtime:
    """The long-running composition over a :class:`~dream.harness.Harness`.

    ::

        async with Runtime(harness) as rt:
            await rt.run_forever()

    Frontends (the REPL, a daemon, tests) are clients: they read the
    event stream and call :meth:`request_stop`; they never own the loops.
    """

    def __init__(
        self,
        harness: Harness,
        config: RuntimeConfig | None = None,
        *,
        paths: DreamPaths | None = None,
        boot_report: BootReport | None = None,
        wake_run_handler: Callable[[HeartbeatDecision], Awaitable[None]] | None = None,
    ) -> None:
        self._harness = harness
        self._config = config or RuntimeConfig()
        self._wake_run_handler = wake_run_handler
        # ``paths`` lets a frontend that resolved storage roots itself (the
        # REPL honours a caller-supplied env mapping) hand them over instead
        # of the runtime re-deriving and diverging. ``boot_report`` likewise:
        # a frontend that already ran the gates passes the verdict so boot
        # doesn't scan twice.
        self._paths = paths if paths is not None else self._resolve_paths(harness)
        self._sink: EventSink | None = None
        self._boot_report: BootReport | None = boot_report
        self._exit_stack = contextlib.ExitStack()
        self._loops: dict[str, asyncio.Task[None]] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._stop_requested = asyncio.Event()
        self._started = False
        self._closed = False

    @staticmethod
    def _resolve_paths(harness: Harness) -> DreamPaths:
        paths = harness.config.paths
        if paths is not None:
            return paths
        return DreamPaths.resolve(harness.config.working_dir)

    # -- introspection ------------------------------------------------------

    @property
    def boot_report(self) -> BootReport | None:
        return self._boot_report

    @property
    def events_path(self) -> Path:
        if self._config.events_path is not None:
            return self._config.events_path
        return self._paths.dream_dir / "runtime" / "events.jsonl"

    @property
    def running_loops(self) -> tuple[str, ...]:
        return tuple(name for name, task in self._loops.items() if not task.done())

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Acquire the lock, run boot gates, start supervised loops."""
        if self._started:
            raise RuntimeError("Runtime.start() called twice")
        self._started = True
        self._paths.ensure()
        try:
            self._acquire_instance_lock()
            sink = EventSink(self.events_path)
            self._sink = sink
            report = self._boot_report
            if report is None:
                report = run_boot_gates(
                    working_dir=self._harness.config.working_dir, paths=self._paths
                )
            self._boot_report = report
            self._emit_boot_events(sink, report)
            if report.blocked:
                sink.emit(
                    "runtime.boot.blocked",
                    findings=[f.message for f in report.blocking_findings()],
                )
                raise RuntimeBootBlockedError(report)
            await self._harness.__aenter__()
            self._subscribe_task_lifecycle(sink)
            self._start_loops(sink)
            sink.emit(
                "runtime.started",
                agent_id=self._config.agent_id,
                loops=list(self._loops),
                resume_candidates=len(report.resume_candidates),
            )
        except BaseException:
            # A failed boot must not leave the lock held or dirs half-open.
            self._exit_stack.close()
            raise

    async def run_forever(self) -> None:
        """Block until :meth:`request_stop` (or task cancellation)."""
        await self._stop_requested.wait()

    def request_stop(self) -> None:
        """Signal-handler-safe stop request; ``run_forever`` returns."""
        self._stop_requested.set()

    async def shutdown(self) -> None:
        """Stop loops, drain tasks, release the lock. Idempotent."""
        if self._closed or not self._started:
            return
        self._closed = True
        self._stop_requested.set()
        for task in self._loops.values():
            task.cancel()
        if self._loops:
            await asyncio.gather(*self._loops.values(), return_exceptions=True)
        for unsub in self._unsubs:
            with contextlib.suppress(Exception):
                unsub()
        self._unsubs.clear()
        await self._drain_tasks()
        await self._harness.aclose()
        if self._sink is not None:
            self._sink.emit("runtime.stopped", agent_id=self._config.agent_id)
        self._exit_stack.close()

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.shutdown()

    # -- internals -----------------------------------------------------------

    def _acquire_instance_lock(self) -> None:
        lock_path = self._paths.dream_dir / _RUNTIME_LOCK_NAME
        acquired = self._exit_stack.enter_context(
            try_exclusive_file_lock(lock_path)
        )
        if not acquired:
            raise RuntimeBusyError(
                f"another runtime already holds {lock_path}"
            )

    def _emit_boot_events(self, sink: EventSink, report: BootReport) -> None:
        for finding in report.repo_findings:
            sink.emit(
                "runtime.boot.warning",
                code=finding.code,
                message=finding.message,
                path=finding.path,
            )
        for name in report.corrupt_sidecars:
            sink.emit("runtime.boot.warning", code="corrupt_sidecar", task_id=name)
        for state in report.resume_candidates:
            sink.emit(
                "runtime.resume.candidate",
                task_id=state.task_id,
                base_branch=state.base_branch,
                last_checkpoint_turn=state.last_checkpoint_turn,
            )

    def _subscribe_task_lifecycle(self, sink: EventSink) -> None:
        manager = self._harness.config.task_manager
        if manager is None:
            return

        def on_started(task: TaskRecord) -> None:
            sink.emit(
                "runtime.task.started",
                task_id=task.id,
                description=task.description,
            )

        def on_finished(task: TaskRecord) -> None:
            sink.emit(
                "runtime.task.finished",
                task_id=task.id,
                status=task.status,
                return_code=task.return_code,
            )

        self._unsubs.append(manager.register_start_listener(on_started))
        self._unsubs.append(manager.register_completion_listener(on_finished))

    def _start_loops(self, sink: EventSink) -> None:
        manager = self._harness.config.task_manager
        registry = self._harness.config.cron_registry_path
        if manager is not None and registry is not None and registry.exists():
            self._spawn("cron", self._cron_factory(manager, registry), sink)
        streamer_factory = self._harness.config.wake_streamer_factory
        idle = self._config.wake_idle_minutes
        if streamer_factory is not None and idle is not None:
            self._spawn("wake", self._wake_factory(streamer_factory, idle, sink), sink)

    def _cron_factory(
        self, manager: BackgroundTaskManager, registry: Path
    ) -> Callable[[], Awaitable[None]]:
        poll_seconds = self._config.cron_poll_seconds
        working_dir = self._harness.config.working_dir

        def factory() -> Awaitable[None]:
            if poll_seconds is None:
                return cron_tick_loop(
                    manager=manager, working_dir=working_dir, registry_path=registry
                )
            return cron_tick_loop(
                manager=manager,
                working_dir=working_dir,
                registry_path=registry,
                poll_seconds=poll_seconds,
            )

        return factory

    def _wake_factory(
        self,
        streamer_factory: Callable[[], object],
        idle_minutes: int,
        sink: EventSink,
    ) -> Callable[[], Awaitable[None]]:
        def factory() -> Awaitable[None]:
            return wake_scheduler_loop(
                streamer_factory=streamer_factory,
                agent_id=self._config.agent_id,
                coordination_dir=self._paths.coordination_dir,
                idle_minutes=idle_minutes,
                heartbeat_config=self._config.heartbeat,
                emit=sink.emit,
                on_run=self._wake_run_handler,
            )

        return factory

    def _spawn(
        self, name: str, factory: Callable[[], Awaitable[None]], sink: EventSink
    ) -> None:
        self._loops[name] = asyncio.create_task(
            supervise_loop(
                name,
                factory,
                emit=sink.emit,
                max_restarts=self._config.max_loop_restarts,
            ),
            name=f"dream-runtime-{name}",
        )

    async def _drain_tasks(self) -> None:
        """Wait for running background tasks; stop survivors at the timeout."""
        manager = self._harness.config.task_manager
        if manager is None:
            return
        deadline = (
            asyncio.get_running_loop().time() + self._config.drain_timeout_seconds
        )
        while manager.list_tasks(status="running"):
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(_DRAIN_POLL_SECONDS)
        for record in manager.list_tasks(status="running"):
            with contextlib.suppress(Exception):
                stopped = await manager.stop_task(record.id)
                if self._sink is not None:
                    self._sink.emit(
                        "runtime.drain.stopped_task",
                        task_id=stopped.id,
                        status=stopped.status,
                    )
