"""Harness facade. The single entry point to the SDK runtime.

Multiple Harness instances must coexist in the same process. Nothing
here reads from module-level state.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from dream.contracts.hook import Hook
from dream.contracts.plugin import Plugin
from dream.contracts.provider import Provider
from dream.contracts.tool import Tool
from dream.errors import SessionResumeError
from dream.services.session_store import (
    FileSessionStore,
    SessionHandle,
    SessionSnapshot,
    SessionSnapshotRevision,
    checked_session_id,
    json_dict_from_mapping,
)
from dream.session import Session, SessionOptions

if TYPE_CHECKING:
    from dream.config.paths import DreamPaths
    from dream.engine._engine import QueryEngine
    from dream.engine._messages import ConversationMessage
    from dream.planner import PlannerCallable
    from dream.roles import RoleManifest, RoleName
    from dream.runner.events import RunTaskObserver
    from dream.runner.role import RunRoleResult
    from dream.runner.task import (
        EvaluatorRun,
        GeneratorExecute,
        PlanAdmission,
        RunTaskResult,
        SprintGoalProvider,
    )
    from dream.subagents._async_delegation import AsyncDelegationManager
    from dream.tasks import BackgroundTaskManager


# Slice D: the production wiring (Provider -> TurnStreamer adapter via
# Spec 02) lands in REPL upgrade #2; until then ``Harness.start_session``
# accepts an injected factory so tests and the demo can bind a real
# engine without forcing every caller through the not-yet-built provider
# pipeline. The hook is underscore-prefixed because it is harness-
# internal and may change without a public API bump.
EngineFactory = Callable[[str, SessionOptions], "QueryEngine"]

# An async teardown the opener returns (e.g. close MCP sessions), or None.
AsyncTeardown = Callable[[], Awaitable[None]]
# Run once before the first session: connects MCP, loads plugins, registers
# their tools/hooks into the harness. Returns an optional teardown. Built by
# ``dream.build_harness``; kept off the public API (underscore on the field).
AsyncOpener = Callable[["Harness"], Awaitable["AsyncTeardown | None"]]


@dataclass
class HarnessConfig:
    """Construction-time configuration for a Harness.

    The Harness reads only what is here. It never reads env vars or files
    on its own; use helpers in `dream.config` for those.
    """

    working_dir: Path = field(default_factory=Path.cwd)
    default_model: str | None = None
    default_provider: str | None = None
    permission_mode: str = "default"
    # Harness-bound runtime subsystems wired by ``dream.build_harness``:
    # the per-harness background task manager (shared across sessions so
    # task IDs / archives stay consistent) and the on-disk cron registry a
    # scheduler tick loop polls. ``None`` when the harness was constructed
    # without the factory (e.g. bare engine-factory tests).
    task_manager: BackgroundTaskManager | None = None
    delegations: AsyncDelegationManager | None = None
    cron_registry_path: Path | None = None
    # The env-resolved storage roots the factory built the harness against,
    # so the runtime reuses the exact same roots (DREAM_HOME honoured) rather
    # than re-resolving and risking divergence.
    paths: DreamPaths | None = None
    session_store: FileSessionStore | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    _engine_factory: EngineFactory | None = None
    # Async setup run once before the first session — MCP connect + plugin
    # load (both async/IO). ``dream.build_harness`` populates it; ``None`` for
    # bare engine-factory harnesses (tests).
    _async_opener: AsyncOpener | None = None


class Harness:
    """The SDK runtime facade.

    Construct with a `HarnessConfig`, register providers / tools / hooks
    / plugins, then `start_session()` to converse. Use as an async
    context manager for deterministic cleanup.
    """

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self.config = config or HarnessConfig()
        self._providers: dict[str, Provider] = {}
        self._tools: dict[str, Tool] = {}
        self._hooks: list[Hook] = []
        self._plugins: list[Plugin] = []
        self._closed = False
        # Async-open state (spec 15 wiring): MCP connect + plugin load are
        # async/IO, so they run once via ``_ensure_open`` — at the
        # ``start_session`` chokepoint (every path funnels through it,
        # including every run_task head and bare ``--once`` callers), not
        # only ``__aenter__`` (which direct callers can skip).
        self._opened = False
        self._open_lock = asyncio.Lock()
        self._teardown: AsyncTeardown | None = None

    # -- registration -----------------------------------------------------

    def register_provider(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def register_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_hook(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def register_plugin(self, plugin: Plugin) -> None:
        self._plugins.append(plugin)
        for tool in plugin.tools:
            self.register_tool(tool)
        for hook in plugin.hooks:
            self.register_hook(hook)
        for provider in plugin.providers:
            self.register_provider(provider)

    # -- lifecycle (async open) -------------------------------------------

    async def _ensure_open(self) -> None:
        """Run the one-time async opener (MCP connect + plugin load).

        Idempotent and lock-guarded: fires exactly once however the harness
        is entered — ``async with``, a ``Runtime``, or a bare
        ``await harness.run_task(...)``. Placed at the ``start_session``
        chokepoint so every session (including every run_task head) is wired
        before its engine is built.
        """
        if self._opened:
            return
        async with self._open_lock:
            if self._opened:
                return
            opener = self.config._async_opener
            if opener is not None:
                self._teardown = await opener(self)
            self._opened = True

    # -- sessions ---------------------------------------------------------

    async def start_session(
        self,
        options: SessionOptions | None = None,
        *,
        session_id: str | None = None,
        store: FileSessionStore | None = None,
        resume_messages: Sequence[ConversationMessage] | None = None,
    ) -> Session:
        """Create a new Session, binding an engine if one is configured.

        When ``HarnessConfig._engine_factory`` is set, the factory is
        invoked with ``(session_id, options)`` and the resulting
        ``QueryEngine`` is attached to the ``Session``. Otherwise the
        Session is returned without an engine binding -- ``send`` will
        raise ``NotImplementedError`` until the production wiring is in
        place.

        ``session_id`` lets a caller mint the id itself so its own records
        (a task-keyed row in a scheduler) and the harness agree without a
        round-trip; a random id is generated when omitted. Ids that could
        escape the sessions root are rejected, and so is an id that already
        names a saved snapshot — two callers landing on the same key would
        otherwise save over each other while both still hold a handle. Continue
        that session with :meth:`resume_session` or clear it with
        :meth:`reset_session` first.

        ``resume_messages`` seeds the session transcript before the first
        ``send`` so a caller (chorus ledger, FileSessionStore) can continue
        an existing conversation rather than starting cold.
        """
        import uuid

        await self._ensure_open()
        opts = options or SessionOptions()
        resolved_id = uuid.uuid4().hex if session_id is None else checked_session_id(session_id)
        if session_id is not None:
            self._refuse_occupied_id(resolved_id, store)
        engine = None
        if self.config._engine_factory is not None:
            engine = self.config._engine_factory(resolved_id, opts)
        return Session(
            id=resolved_id,
            options=opts,
            _engine=engine,
            resume_messages=list(resume_messages) if resume_messages else None,
        )

    def _maybe_session_store(self, store: FileSessionStore | None) -> FileSessionStore | None:
        if store is not None:
            return store
        if self.config.session_store is not None:
            return self.config.session_store
        if self.config.paths is not None:
            return FileSessionStore(self.config.paths.sessions_dir)
        return None

    def _resolve_session_store(self, store: FileSessionStore | None) -> FileSessionStore:
        resolved = self._maybe_session_store(store)
        if resolved is None:
            raise ValueError(
                "save_session/resume_session requires store=..., "
                "HarnessConfig.session_store, or HarnessConfig.paths"
            )
        return resolved

    def _refuse_occupied_id(self, session_id: str, store: FileSessionStore | None) -> None:
        """Keep a caller-minted id off a snapshot that is still someone's.

        Nothing to check when no store is configured: with nowhere to persist,
        an id collision costs nothing.
        """
        resolved = self._maybe_session_store(store)
        if resolved is None or not resolved.exists(session_id):
            return
        raise ValueError(
            f"session {session_id!r} already has a saved snapshot; "
            "resume_session to continue it, or reset_session to discard it"
        )

    async def save_session(
        self,
        session: Session,
        *,
        store: FileSessionStore | None = None,
    ) -> SessionHandle:
        """Persist a session snapshot; return the handle to resume it.

        The returned :class:`SessionHandle` is the only thing a caller needs to
        keep: the transcript stays in the harness's own store. ``usage_delta``
        reports the spend since this session's previous save, so a scheduler can
        bill per run without differencing totals itself. The write compares the
        store against the revision this session opened; a concurrent replacement
        raises :class:`dream.errors.SessionSaveConflictError` unchanged.
        """
        resolved = self._resolve_session_store(store)
        snapshot = session.snapshot()
        usage_delta = session._usage_delta()
        write = resolved.compare_and_swap_save(snapshot, session._snapshot_expectation())
        # Only advance the billing baseline once the bytes are durable, so a
        # failed write leaves the next save reporting the same delta.
        session._mark_persisted(snapshot.cost, write.revision)
        return SessionHandle(
            session_id=snapshot.session_id,
            path=write.path,
            working_dir=snapshot.working_dir,
            schema_version=snapshot.schema_version,
            saved_at=snapshot.saved_at,
            usage_delta=usage_delta,
            usage_total=snapshot.cost,
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        options: SessionOptions | None = None,
        store: FileSessionStore | None = None,
        allow_working_dir_change: bool = False,
    ) -> Session:
        """Load a saved snapshot and bind a fresh engine (process restart).

        When ``options`` is omitted, persisted model, prompt, turn budget, and
        JSON-compatible metadata are restored. Response formats and any
        non-JSON metadata must be passed explicitly.

        Raises :class:`SessionResumeError` when the snapshot is missing,
        unreadable, written by another schema, or belongs to a different
        working directory — a transcript about other files is worse than no
        transcript, so that last case needs ``allow_working_dir_change=True``.
        """
        resolved = self._resolve_session_store(store)
        loaded = resolved.load_with_revision(session_id)
        snapshot = loaded.snapshot
        if not allow_working_dir_change:
            self._check_working_dir(snapshot, session_id, loaded.revision)
        await self._ensure_open()
        opts = options or SessionOptions(
            model=snapshot.model,
            system_prompt=snapshot.system_prompt,
            max_turns=snapshot.max_turns,
            metadata=json_dict_from_mapping(snapshot.metadata),
        )
        engine = None
        if self.config._engine_factory is not None:
            engine = self.config._engine_factory(session_id, opts)
        session = Session(
            id=session_id,
            options=opts,
            _engine=engine,
            _snapshot_revision=loaded.revision,
        )
        session.restore_from_snapshot(snapshot)
        return session

    def _check_working_dir(
        self,
        snapshot: SessionSnapshot,
        session_id: str,
        revision: SessionSnapshotRevision,
    ) -> None:
        """Refuse a resume whose snapshot was taken in another directory."""
        saved = snapshot.working_dir
        if saved is None:
            raise SessionResumeError(
                f"session {session_id!r} has no recorded working directory; "
                "pass allow_working_dir_change=True to resume anyway",
                reason="working_dir_mismatch",
                session_id=session_id,
                revision=revision,
            )
        current = self.config.working_dir
        if Path(saved).expanduser().resolve() == Path(current).expanduser().resolve():
            return
        raise SessionResumeError(
            f"session was saved under {saved!r} but this harness works in "
            f"{str(current)!r}; pass allow_working_dir_change=True to resume anyway",
            reason="working_dir_mismatch",
            session_id=session_id,
            revision=revision,
        )

    async def reset_session(
        self,
        session_id: str,
        *,
        store: FileSessionStore | None = None,
    ) -> bool:
        """Drop a saved snapshot; return whether one was removed.

        The recovery half of the handle contract: after a failed resume, or
        when a caller decides the context has gone stale, clear the snapshot so
        the next ``start_session`` under the same id begins clean.
        """
        resolved = self._resolve_session_store(store)
        return resolved.delete(session_id)

    async def _reset_session_if_unchanged(
        self,
        session_id: str,
        expected_revision: SessionSnapshotRevision | None,
        *,
        store: FileSessionStore | None = None,
    ) -> bool:
        """Clear a failed snapshot only when no writer has replaced its bytes."""
        resolved = self._resolve_session_store(store)
        return resolved.reset_if_unchanged(session_id, expected_revision)

    async def run_role(
        self,
        role: RoleName | RoleManifest,
        intent: str,
        *,
        options: SessionOptions | None = None,
        harness_dir: Path | None = None,
        observer: RunTaskObserver | None = None,
        session_id: str | None = None,
        resume_messages: Sequence[ConversationMessage] | None = None,
    ) -> RunRoleResult:
        """Run one session as a named role; return its assistant text + cost.

        Resolves the role's manifest (bundled default; overlay-merged
        from ``{harness_dir}/roles/{role}.toml`` when given), prepends
        the manifest's system prompt to ``options.system_prompt``, marks
        the manifest on ``SessionOptions.metadata`` (keys
        ``dream.role`` / ``dream.role_manifest``) so a role-aware engine
        factory can intersect the registered tools with the role's
        allow-list and pick its permission mode, then drains the session
        to completion.

        The primitive the production planner / generator / evaluator
        heads compose into :func:`dream.runner.run_task` — see spec 10
        slice G2.

        ``session_id`` names the role thread so a later call continues it
        instead of starting over; the run's :class:`SessionHandle` comes back
        on the result. Nothing is persisted when it is omitted.

        ``resume_messages`` seeds a freshly opened session transcript before
        ``send``. A successful snapshot resume for ``session_id`` already
        carries that history, so the seed is used only on a cold start.
        """
        # Local import keeps the harness <-> runner module graph
        # one-way: ``dream.runner`` imports from ``dream.planner`` /
        # ``dream.sprint`` / ``dream.swarm``; pulling it in at module
        # scope here would add those to every Harness import.
        from dream.runner.role import run_role as _run_role

        return await _run_role(
            self,
            role,
            intent,
            options=options,
            harness_dir=harness_dir,
            observer=observer,
            session_id=session_id,
            resume_messages=resume_messages,
        )

    async def run_task(
        self,
        *,
        task_id: str | None = None,
        intent: str,
        planner: PlannerCallable | None = None,
        generator_execute: GeneratorExecute | None = None,
        evaluator_run: EvaluatorRun | None = None,
        worktree_root: Path | None = None,
        harness_dir: Path | None = None,
        max_sprints: int | None = None,
        verification_steps: tuple[Mapping[str, str], ...] | None = None,
        goal_for_step: SprintGoalProvider | None = None,
        observer: RunTaskObserver | None = None,
        rubric: str | None = None,
        plan_admission: PlanAdmission | None = None,
        session_scope: str | None = None,
        resume_messages: Sequence[ConversationMessage] | None = None,
    ) -> RunTaskResult:
        """Run an end-to-end task: planner → bounded sprint loop.

        Thin facade over :func:`dream.runner.run_task`. ``worktree_root``
        defaults to ``self.config.working_dir`` so a Harness with a
        configured ``working_dir`` is a complete unit.

        When a head is ``None`` the corresponding production factory
        (:func:`dream.runner.make_planner_head` etc.) is invoked against
        this harness so a one-liner ``await harness.run_task(intent=...)``
        wires every LLM head from the configured engine. ``task_id``
        defaults to a minted ``t-YYYYMMDDTHHMMSS-XXXX`` slug.

        ``observer`` is forwarded to the runner and to every head so a
        single :class:`~dream.runner.StdioObserver` (or custom hook) sees
        every macro and streaming event.

        ``session_scope`` makes the task's role sessions resumable: each
        autowired head runs in its own thread under that scope
        (``{scope}-planner`` and so on), so calling ``run_task`` again with the
        same scope continues those conversations rather than restarting them.
        A caller driving the harness in short windows keeps one key per task.
        Explicitly supplied heads are left alone — they own their own sessions.

        ``resume_messages`` seeds the autowired generator session with prior
        typed transcript (chorus ledger / FileSessionStore). Custom
        ``generator_execute`` heads must handle resume themselves.
        """
        from dataclasses import replace as _replace

        from dream.runner.observe import UsageMeter
        from dream.runner.task import run_task as _run_task

        root = worktree_root if worktree_root is not None else self.config.working_dir
        effective_task_id = task_id if task_id is not None else _mint_task_id()

        # Wrap the caller's observer (may be None) in a UsageMeter so token
        # counts are accumulated from every role.session.closed event the
        # runner emits. The meter forwards all events to the inner observer,
        # so the caller's observer still sees every event it expects.
        meter = UsageMeter(observer)

        planner, generator_execute, evaluator_run = self._resolve_heads(
            planner=planner,
            generator_execute=generator_execute,
            evaluator_run=evaluator_run,
            intent=intent,
            harness_dir=harness_dir,
            observer=meter,
            worktree_root=root,
            session_scope=session_scope,
            resume_messages=resume_messages,
        )

        # ``kwargs`` is hand-built (rather than passing real keyword args) so the
        # facade forwards ONLY the optionals the caller actually set — defaults
        # for ``max_sprints`` / ``verification_steps`` / ``goal_for_step`` live
        # in ``runner.run_task``, not here. The meter is always forwarded as
        # ``observer`` so it meters head events and fans out to the user observer.
        kwargs: dict[str, Any] = {
            "task_id": effective_task_id,
            "intent": intent,
            "worktree_root": root,
            "planner": planner,
            "generator_execute": generator_execute,
            "evaluator_run": evaluator_run,
            "observer": meter,
        }
        if max_sprints is not None:
            kwargs["max_sprints"] = max_sprints
        if verification_steps is not None:
            kwargs["verification_steps"] = verification_steps
        if goal_for_step is not None:
            kwargs["goal_for_step"] = goal_for_step
        if rubric is not None:
            kwargs["rubric"] = rubric
        if plan_admission is not None:
            kwargs["plan_admission"] = plan_admission
        result = await _run_task(**kwargs)
        return _replace(result, usage_by_model=meter.usage_by_model)

    def _resolve_heads(
        self,
        *,
        planner: PlannerCallable | None,
        generator_execute: GeneratorExecute | None,
        evaluator_run: EvaluatorRun | None,
        intent: str,
        harness_dir: Path | None,
        observer: RunTaskObserver | None,
        worktree_root: Path | None = None,
        session_scope: str | None = None,
        resume_messages: Sequence[ConversationMessage] | None = None,
    ) -> tuple[PlannerCallable, GeneratorExecute, EvaluatorRun]:
        """Fill any ``None`` head with its production factory (10-I autowire).

        A one-liner ``await harness.run_task(intent=...)`` wires every LLM head
        from the configured engine; explicitly supplied heads pass through
        untouched. Returns the three resolved heads in run_task argument order.

        ``session_scope`` is forwarded to each factory so its role runs in a
        resumable thread under that scope. ``resume_messages`` seeds only the
        autowired generator; later sprints capture the live transcript.
        """
        if (
            planner is not None
            and generator_execute is not None
            and evaluator_run is not None
        ):
            return (planner, generator_execute, evaluator_run)

        from dream.runner import (
            make_evaluator_head,
            make_generator_head,
            make_planner_head,
        )

        if planner is None:
            planner = make_planner_head(
                self,
                harness_dir=harness_dir,
                observer=observer,
                session_scope=session_scope,
            )
        if generator_execute is None:
            generator_execute = make_generator_head(
                self,
                task_intent=intent,
                harness_dir=harness_dir,
                observer=observer,
                session_scope=session_scope,
                resume_messages=resume_messages,
            )
        if evaluator_run is None:
            evaluator_run = make_evaluator_head(
                self,
                task_intent=intent,
                harness_dir=harness_dir,
                observer=observer,
                session_scope=session_scope,
            )
        return (planner, generator_execute, evaluator_run)

    # -- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        if self.config.delegations is not None:
            await self.config.delegations.close()
        if self._teardown is not None:
            teardown, self._teardown = self._teardown, None
            await teardown()
        self._closed = True

    async def __aenter__(self) -> Self:
        await self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _mint_task_id() -> str:
    """Return a sortable task id slug used when ``run_task`` omits one."""
    return f"t-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"


__all__ = ["Harness", "HarnessConfig"]
