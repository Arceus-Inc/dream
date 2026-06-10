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
from typing import Any

from dream.channels import (
    Ack,
    CancelCommand,
    Command,
    CommandInbox,
    StatusCommand,
    SubmitTaskCommand,
    WakeCommand,
)
from dream.config.paths import DreamPaths
from dream.coordination import Claim
from dream.errors import DreamError
from dream.harness import Harness, _mint_task_id
from dream.observability import EventSink
from dream.runtime._boot import BootReport, run_boot_gates
from dream.runtime._channel import channel_loop
from dream.runtime._supervisor import supervise_loop
from dream.runtime._wake_scheduler import wake_scheduler_loop
from dream.runtime._watchdog import watchdog_loop
from dream.runtime._workers import WorkerSupervisor
from dream.services.cron import cron_tick_loop
from dream.swarm import TeamRegistry
from dream.swarm._spawn import TeammateExecutor, TeammateSpawnConfig
from dream.tasks import BackgroundTaskManager, TaskRecord
from dream.utils.file_lock import try_exclusive_file_lock
from dream.wake import (
    HeartbeatConfig,
    HeartbeatDecision,
    ManualWake,
    run_wake_cycle,
)

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
    # Operator/persona override for the heartbeat prompt the wake cycle
    # sends; None uses the bundled default.
    wake_prompt_path: Path | None = None
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    channel_poll_seconds: float = 1.0
    drain_timeout_seconds: float = 10.0
    max_loop_restarts: int = 5
    # Rotate the events JSONL once it would exceed this size (one prior
    # generation kept as ``events.jsonl.1``). None disables rotation.
    events_max_bytes: int | None = 10_000_000
    # Liveness watchdog (spec 10p5): how often to walk the coordination
    # board for expired leases. None disables the watchdog loop.
    watchdog_poll_seconds: float | None = 30.0
    # Job budgets (spec 15 P3 §3): wall-clock cap per submitted job
    # (None = uncapped) and how many times a *failed* job is retried.
    # A timeout is a budget decision, never retried.
    job_timeout_seconds: float | None = None
    job_max_retries: int = 0


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
        stale_claim_handler: Callable[[Claim], Awaitable[None]] | None = None,
    ) -> None:
        self._harness = harness
        self._config = config or RuntimeConfig()
        self._wake_run_handler = wake_run_handler
        self._stale_claim_handler = stale_claim_handler
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
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._wake_runs: list[asyncio.Task[None]] = []
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
    def inbox_path(self) -> Path:
        return self._paths.dream_dir / "runtime" / "inbox"

    @property
    def running_loops(self) -> tuple[str, ...]:
        return tuple(name for name, task in self._loops.items() if not task.done())

    @property
    def running_jobs(self) -> tuple[str, ...]:
        return tuple(
            task_id for task_id, task in self._jobs.items() if not task.done()
        )

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Acquire the lock, run boot gates, start supervised loops."""
        if self._started:
            raise RuntimeError("Runtime.start() called twice")
        self._started = True
        self._paths.ensure()
        try:
            self._acquire_instance_lock()
            sink = EventSink(self.events_path, max_bytes=self._config.events_max_bytes)
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

    def start_worker(
        self,
        config: TeammateSpawnConfig,
        *,
        executor: TeammateExecutor,
        max_restarts: int = 3,
        registry: TeamRegistry | None = None,
    ) -> asyncio.Task[None]:
        """Spawn a swarm teammate as a supervised child (spec 15 P5).

        Worker lifecycle events (``runtime.worker.*``) go to the runtime
        event stream; the worker is cancelled (and its child stopped) on
        runtime shutdown. Requires a harness with a task manager and a
        started runtime.
        """
        sink = self._sink
        manager = self._harness.config.task_manager
        if sink is None or not self._started:
            raise RuntimeError("start_worker requires a started runtime")
        if manager is None:
            raise RuntimeError("start_worker requires a harness task manager")
        supervisor = WorkerSupervisor(
            executor=executor,
            task_manager=manager,
            emit=sink.emit,
            registry=registry,
            max_restarts=max_restarts,
        )
        task = asyncio.create_task(
            supervisor.run_worker(config),
            name=f"dream-worker-{config.team}-{config.name}",
        )
        self._workers.append(task)
        return task

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
        # Loops first (stop draining new commands), then in-flight jobs —
        # each job's wrapper emits ``runtime.job.cancelled`` on its way out.
        for task in self._loops.values():
            task.cancel()
        if self._loops:
            await asyncio.gather(*self._loops.values(), return_exceptions=True)
        for job in self._jobs.values():
            job.cancel()
        if self._jobs:
            await asyncio.gather(*self._jobs.values(), return_exceptions=True)
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        for wake_run in self._wake_runs:
            wake_run.cancel()
        if self._wake_runs:
            await asyncio.gather(*self._wake_runs, return_exceptions=True)
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
        if self._config.watchdog_poll_seconds is not None:
            self._spawn("watchdog", self._watchdog_factory(sink), sink)
        self._spawn("channel", self._channel_factory(sink), sink)

    def _watchdog_factory(self, sink: EventSink) -> Callable[[], Awaitable[None]]:
        poll_seconds = self._config.watchdog_poll_seconds
        assert poll_seconds is not None
        board_path = self._paths.coordination_board

        def factory() -> Awaitable[None]:
            return watchdog_loop(
                board_path=board_path,
                emit=sink.emit,
                on_stale=self._stale_claim_handler,
                poll_seconds=poll_seconds,
            )

        return factory

    def _channel_factory(self, sink: EventSink) -> Callable[[], Awaitable[None]]:
        inbox = CommandInbox(self.inbox_path)
        poll_seconds = self._config.channel_poll_seconds

        def factory() -> Awaitable[None]:
            return channel_loop(
                inbox=inbox,
                sink=sink,
                handler=self._handle_command,
                poll_seconds=poll_seconds,
            )

        return factory

    # -- command handling ------------------------------------------------

    async def _handle_command(self, command: Command) -> Ack:
        if isinstance(command, StatusCommand):
            return self._status_ack()
        if isinstance(command, SubmitTaskCommand):
            return self._handle_submit(command)
        if isinstance(command, CancelCommand):
            return await self._handle_cancel(command)
        return await self._handle_wake(command)

    def _status_ack(self) -> Ack:
        manager = self._harness.config.task_manager
        background_running = (
            len(manager.list_tasks(status="running")) if manager is not None else 0
        )
        loops = ", ".join(self.running_loops) or "none"
        summary = (
            f"loops: {loops}; jobs running: {len(self.running_jobs)}; "
            f"background tasks running: {background_running}"
        )
        return Ack(
            status="ok",
            summary=summary,
            next_actions=(
                "submit_task {intent} to start work",
                "cancel {task_id} to stop a job",
            ),
            artifacts=(str(self.events_path),),
        )

    def _handle_submit(self, command: SubmitTaskCommand) -> Ack:
        task_id = command.task_id or _mint_task_id()
        existing = self._jobs.get(task_id)
        if existing is not None and not existing.done():
            return Ack(
                status="rejected", summary=f"job {task_id} is already running"
            )
        self._spawn_job(task_id, command)
        return Ack(
            status="ok",
            summary=f"task {task_id} accepted: {command.intent}",
            next_actions=("status to watch progress", f"cancel {task_id} to stop"),
            artifacts=(str(self.events_path),),
        )

    def _spawn_job(self, task_id: str, command: SubmitTaskCommand) -> None:
        sink = self._sink
        assert sink is not None  # jobs only spawn after start()

        timeout = self._config.job_timeout_seconds
        max_retries = self._config.job_max_retries

        async def run() -> None:
            if command.max_sprints is None:
                await self._harness.run_task(task_id=task_id, intent=command.intent)
                return
            await self._harness.run_task(
                task_id=task_id,
                intent=command.intent,
                max_sprints=command.max_sprints,
            )

        async def run_within_budget() -> None:
            if timeout is None:
                await run()
                return
            async with asyncio.timeout(timeout):
                await run()

        async def job() -> None:
            # Retry only plain failures — a timeout is a budget decision
            # (retrying it would double the spend), and cancellation is an
            # operator/shutdown decision.
            for attempt in range(max_retries + 1):
                try:
                    await run_within_budget()
                except asyncio.CancelledError:
                    sink.emit("runtime.job.cancelled", task_id=task_id)
                    raise
                except TimeoutError:
                    sink.emit(
                        "runtime.job.failed",
                        task_id=task_id,
                        error=f"wall-clock budget exceeded ({timeout}s)",
                    )
                    return
                except Exception as exc:
                    if attempt < max_retries:
                        sink.emit(
                            "runtime.job.retry",
                            task_id=task_id,
                            attempt=attempt + 1,
                            error=repr(exc),
                        )
                        continue
                    sink.emit("runtime.job.failed", task_id=task_id, error=repr(exc))
                    return
                else:
                    sink.emit("runtime.job.finished", task_id=task_id)
                    return

        self._jobs[task_id] = asyncio.create_task(job(), name=f"dream-job-{task_id}")

    async def _handle_cancel(self, command: CancelCommand) -> Ack:
        job = self._jobs.get(command.task_id)
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            return Ack(status="ok", summary=f"job {command.task_id} cancelled")
        manager = self._harness.config.task_manager
        if manager is not None:
            record = manager.get_task(command.task_id)
            if record is not None and record.status == "running":
                stopped = await manager.stop_task(command.task_id)
                return Ack(
                    status="ok",
                    summary=f"background task {stopped.id} stopped ({stopped.status})",
                )
        return Ack(
            status="rejected",
            summary=f"no running job or background task {command.task_id}",
        )

    async def _handle_wake(self, command: WakeCommand) -> Ack:
        streamer_factory = self._harness.config.wake_streamer_factory
        sink = self._sink
        if streamer_factory is None or sink is None:
            return Ack(
                status="rejected",
                summary="wake not configured (harness has no wake streamer)",
            )
        def forward(event_type: str, payload: dict[str, Any]) -> None:
            sink.emit(event_type, **payload)

        outcome = await run_wake_cycle(
            streamer_factory(),
            agent_id=self._config.agent_id,
            wake_source=ManualWake(),
            coordination_dir=self._paths.coordination_dir,
            config=self._config.heartbeat,
            prompt_override_path=self._config.wake_prompt_path,
            on_event=forward,
        )
        if outcome.decision is None:
            return Ack(
                status="rejected",
                summary=f"wake dropped: {outcome.dropped_reason}",
            )
        decision = outcome.decision
        summary = f"wake decided {decision.action}: {decision.reason}"
        # A `run` decision is executed exactly like a scheduled wake's: the
        # handler runs as a tracked background task (it may drive sessions
        # for minutes) so the command ack returns promptly.
        if decision.action == "run" and self._wake_run_handler is not None:
            handler = self._wake_run_handler

            async def execute() -> None:
                await handler(decision)

            self._wake_runs.append(
                asyncio.create_task(execute(), name="dream-wake-run")
            )
            summary += f" — executing {len(decision.tasks)} task(s) in background"
        return Ack(
            status="ok",
            summary=summary,
            artifacts=(str(self.events_path),),
        )

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
                prompt_override_path=self._config.wake_prompt_path,
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
