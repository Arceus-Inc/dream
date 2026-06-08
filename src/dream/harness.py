"""Harness facade. The single entry point to the SDK runtime.

Multiple Harness instances must coexist in the same process. Nothing
here reads from module-level state.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from dream.contracts.hook import Hook
from dream.contracts.plugin import Plugin
from dream.contracts.provider import Provider
from dream.contracts.tool import Tool
from dream.session import Session, SessionOptions

if TYPE_CHECKING:
    from dream.engine._engine import QueryEngine
    from dream.planner import PlannerCallable
    from dream.roles import RoleManifest, RoleName
    from dream.runner._observer import RunTaskObserver
    from dream.runner._role_session import RunRoleResult
    from dream.runner._run import (
        EvaluatorRun,
        GeneratorExecute,
        RunTaskResult,
        SprintGoalProvider,
    )
    from dream.sprint import EvaluatorPropose, GeneratorRespond


# Slice D: the production wiring (Provider -> TurnStreamer adapter via
# Spec 02) lands in REPL upgrade #2; until then ``Harness.start_session``
# accepts an injected factory so tests and the demo can bind a real
# engine without forcing every caller through the not-yet-built provider
# pipeline. The hook is underscore-prefixed because it is harness-
# internal and may change without a public API bump.
EngineFactory = Callable[[str, SessionOptions], "QueryEngine"]


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
    extra: dict[str, Any] = field(default_factory=dict)
    _engine_factory: EngineFactory | None = None


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

    # -- sessions ---------------------------------------------------------

    async def start_session(self, options: SessionOptions | None = None) -> Session:
        """Create a new Session, binding an engine if one is configured.

        When ``HarnessConfig._engine_factory`` is set, the factory is
        invoked with ``(session_id, options)`` and the resulting
        ``QueryEngine`` is attached to the ``Session``. Otherwise the
        Session is returned without an engine binding -- ``send`` will
        raise ``NotImplementedError`` until the production wiring is in
        place.
        """
        import uuid

        opts = options or SessionOptions()
        session_id = uuid.uuid4().hex
        engine = None
        if self.config._engine_factory is not None:
            engine = self.config._engine_factory(session_id, opts)
        return Session(id=session_id, options=opts, _engine=engine)

    async def run_role(
        self,
        role: RoleName | RoleManifest,
        intent: str,
        *,
        options: SessionOptions | None = None,
        harness_dir: Path | None = None,
        observer: RunTaskObserver | None = None,
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
        """
        # Local import keeps the harness <-> runner module graph
        # one-way: ``dream.runner`` imports from ``dream.planner`` /
        # ``dream.sprint`` / ``dream.swarm``; pulling it in at module
        # scope here would add those to every Harness import.
        from dream.runner._role_session import run_role as _run_role

        return await _run_role(
            self,
            role,
            intent,
            options=options,
            harness_dir=harness_dir,
            observer=observer,
        )

    async def run_task(
        self,
        *,
        task_id: str | None = None,
        intent: str,
        planner: PlannerCallable | None = None,
        generator_execute: GeneratorExecute | None = None,
        evaluator_propose: EvaluatorPropose | None = None,
        generator_respond: GeneratorRespond | None = None,
        evaluator_run: EvaluatorRun | None = None,
        worktree_root: Path | None = None,
        harness_dir: Path | None = None,
        max_sprints: int | None = None,
        verification_steps: tuple[dict[str, str], ...] | None = None,
        goal_for_step: SprintGoalProvider | None = None,
        observer: RunTaskObserver | None = None,
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
        """
        from dream.runner._run import run_task as _run_task

        root = worktree_root if worktree_root is not None else self.config.working_dir
        effective_task_id = task_id if task_id is not None else _mint_task_id()

        if (
            planner is None
            or generator_execute is None
            or evaluator_propose is None
            or generator_respond is None
            or evaluator_run is None
        ):
            from dream.runner import (
                make_evaluator_head,
                make_evaluator_propose_head,
                make_generator_head,
                make_generator_respond_head,
                make_planner_head,
            )

            if planner is None:
                planner = make_planner_head(
                    self, harness_dir=harness_dir, observer=observer
                )
            if generator_execute is None:
                generator_execute = make_generator_head(
                    self, harness_dir=harness_dir, observer=observer
                )
            if evaluator_propose is None:
                evaluator_propose = make_evaluator_propose_head(
                    self, harness_dir=harness_dir, observer=observer
                )
            if generator_respond is None:
                generator_respond = make_generator_respond_head(
                    self, harness_dir=harness_dir, observer=observer
                )
            if evaluator_run is None:
                evaluator_run = make_evaluator_head(
                    self, harness_dir=harness_dir, observer=observer
                )

        kwargs: dict[str, Any] = {
            "task_id": effective_task_id,
            "intent": intent,
            "worktree_root": root,
            "planner": planner,
            "generator_execute": generator_execute,
            "evaluator_propose": evaluator_propose,
            "generator_respond": generator_respond,
            "evaluator_run": evaluator_run,
        }
        if max_sprints is not None:
            kwargs["max_sprints"] = max_sprints
        if verification_steps is not None:
            kwargs["verification_steps"] = verification_steps
        if goal_for_step is not None:
            kwargs["goal_for_step"] = goal_for_step
        if observer is not None:
            kwargs["observer"] = observer
        return await _run_task(**kwargs)

    # -- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        self._closed = True

    async def __aenter__(self) -> Self:
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
