"""Internal ``QueryEngine`` -- the per-session glue object. Not exported.

``QueryEngine`` is what a ``Session`` holds. It bundles the three
collaborators that the ``run_session`` orchestrator (Spec 03) and the
inner ``run_query`` act-loop need:

- a ``TurnStreamer`` that yields one model turn's events at a time;
- a ``ToolDispatcher`` that runs a tool by name and returns
  ``(content, is_error)``;
- a ``session_id`` + ``working_dir`` so the dispatcher can mint
  scratch paths and the orchestrator can stamp records.

``make_session_config`` builds a fresh ``SessionConfig`` per ``send`` so
checkpoints can be threaded per-call without rebuilding the engine.

``build_query_engine`` is the convenience factory that wraps a
``ToolRegistry`` in the canonical ``EngineToolDispatcher`` so callers
never instantiate the dispatcher by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dream.contracts.provider import ProviderCapabilities
from dream.engine._loop import ToolDispatcher, TurnStreamer
from dream.engine._orientation import OrientationConfig
from dream.engine._records import TurnRecord
from dream.engine._session import SessionConfig
from dream.engine._tool_dispatch import DispatchRecord, EngineToolDispatcher, PermissionGate
from dream.hooks import HookExecutor
from dream.observability._tracer import NoopTracer, Tracer
from dream.permissions import SessionLimiter, SessionLimits
from dream.services.compact import DEFAULT_KEEP_RECENT
from dream.services.compact._carryover_state import CarryoverMetadata, UtilisationRatio
from dream.services.compact._orchestrator import AutoCompactState, SummariserFn
from dream.tools._registry import ToolRegistry


@dataclass
class QueryEngine:
    """Per-session glue: streamer + dispatcher + session metadata.

    Constructed by ``Harness.start_session`` and handed to ``Session``
    via the internal ``_engine`` kwarg. ``Session.send`` calls
    :meth:`make_session_config` to build a ``SessionConfig`` for the
    current call, then drives ``run_session`` with it.

    Compaction is opt-in via ``compactor``. When set, every turn runs the
    Spec 04 orchestrator before re-entering the model; the per-engine
    ``AutoCompactState`` survives across ``send`` calls so the same-turn
    cooldown and consecutive-failure counter behave correctly.
    """

    streamer: TurnStreamer
    dispatcher: ToolDispatcher
    session_id: str
    working_dir: Path
    max_turns: int = 8
    compactor: AutoCompactState | None = None
    compaction_threshold: UtilisationRatio = 0.7
    compaction_preserve_recent: int = DEFAULT_KEEP_RECENT
    compaction_capabilities: ProviderCapabilities | None = None
    compaction_summariser: SummariserFn | None = None
    carryover_metadata: CarryoverMetadata | None = None
    tracer: Tracer = field(default_factory=NoopTracer)
    model: str = ""
    # Spec 13D: per-session hard caps. A fresh SessionLimiter is minted per
    # ``make_session_config`` call (per send), so counters reset each session.
    limits: SessionLimits | None = None
    # Spec 13: optional lifecycle hook executor. Held here (and handed to the
    # dispatcher at build time) so SESSION_START / STOP fire in the session
    # loop and PRE/POST_TOOL_USE fire around each dispatch. ``None`` disables
    # all firing, leaving the loop byte-for-byte unchanged.
    hook_executor: HookExecutor | None = None
    # spec 15 §4.2: optional orientation ritual run once at session start (``run_session`` calls it when
    # set). chorus uses it to prepend ``AGENTS.md`` to every beat so an employee reads the cross-child
    # contract before writing. ``None`` leaves the session loop byte-for-byte unchanged (the default).
    orientation: OrientationConfig | None = None
    # Role name from session metadata (planner/generator/evaluator) for STOP hooks.
    role: str | None = None

    def make_session_config(
        self,
        *,
        checkpoint: Callable[[TurnRecord], None] | None = None,
    ) -> SessionConfig:
        """Build a ``SessionConfig`` wired to this engine's collaborators.

        Orientation / heartbeat / reviewer are left ``None`` for slice D;
        they get plumbed in later slices via the same factory. Compaction
        fields are forwarded so the orchestrator sees the same state on
        every ``send`` call.
        """
        return SessionConfig(
            client=self.streamer,
            tools=self.dispatcher,
            max_turns=self.max_turns,
            session_id=self.session_id,
            checkpoint=checkpoint,
            compactor=self.compactor,
            compaction_threshold=self.compaction_threshold,
            compaction_preserve_recent=self.compaction_preserve_recent,
            compaction_capabilities=self.compaction_capabilities,
            compaction_summariser=self.compaction_summariser,
            carryover_metadata=self.carryover_metadata,
            working_dir=self.working_dir,
            tracer=self.tracer,
            model=self.model,
            limiter=SessionLimiter(self.limits) if self.limits is not None else None,
            hook_executor=self.hook_executor,
            orientation=self.orientation,
            role=self.role,
        )


def build_query_engine(
    *,
    streamer: TurnStreamer,
    registry: ToolRegistry,
    session_id: str,
    working_dir: Path,
    scratch_dir: Path | None = None,
    max_turns: int = 8,
    on_dispatch: Callable[[DispatchRecord], None] | None = None,
    context_metadata: dict[str, Any] | None = None,
    permission_gate: PermissionGate | None = None,
    role_allowed_tools: frozenset[str] | None = None,
    limits: SessionLimits | None = None,
    compactor: AutoCompactState | None = None,
    compaction_threshold: UtilisationRatio = 0.7,
    compaction_preserve_recent: int = DEFAULT_KEEP_RECENT,
    compaction_capabilities: ProviderCapabilities | None = None,
    compaction_summariser: SummariserFn | None = None,
    carryover_metadata: CarryoverMetadata | None = None,
    tracer: Tracer | None = None,
    model: str = "",
    hook_executor: HookExecutor | None = None,
    orientation: OrientationConfig | None = None,
) -> QueryEngine:
    """Wrap a ``ToolRegistry`` in the canonical dispatcher and bind a streamer.

    The dispatcher reference is held by-identity so callers can hot-register
    tools on the same ``ToolRegistry`` between sessions and see them on the
    next dispatch without rebuilding the engine.
    """
    dispatcher = EngineToolDispatcher(
        registry=registry,
        working_dir=working_dir,
        session_id=session_id,
        scratch_dir=scratch_dir,
        on_dispatch=on_dispatch,
        context_metadata=context_metadata or {},
        permission_gate=permission_gate,
        role_allowed_tools=role_allowed_tools,
        hook_executor=hook_executor,
    )
    meta = context_metadata or {}
    role_raw = meta.get("dream.role")
    return QueryEngine(
        streamer=streamer,
        dispatcher=dispatcher,
        session_id=session_id,
        working_dir=working_dir,
        max_turns=max_turns,
        compactor=compactor,
        compaction_threshold=compaction_threshold,
        compaction_preserve_recent=compaction_preserve_recent,
        compaction_capabilities=compaction_capabilities,
        compaction_summariser=compaction_summariser,
        carryover_metadata=carryover_metadata,
        tracer=tracer or NoopTracer(),
        model=model,
        limits=limits,
        hook_executor=hook_executor,
        orientation=orientation,
        role=role_raw if isinstance(role_raw, str) else None,
    )


__all__ = ["QueryEngine", "build_query_engine"]
