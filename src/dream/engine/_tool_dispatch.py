"""``EngineToolDispatcher`` -- bridges ``ToolRegistry`` to the engine loop.

Implements the narrow ``ToolDispatcher`` Protocol declared by
``dream.engine._loop`` over the richer ``Tool`` / ``ToolResult`` types from
``dream.contracts.tool`` + ``dream.tools._base``. Shape borrowed from
OpenHarness ``engine/query.py::_execute_tool_call``, simplified for
spec 05's narrower surface (no permission checker / hook executor here --
those come back in later slices).

Pipeline per ``dispatch`` call:

1. ``registry.get(name)`` -- unknown name returns a typed-error result
   with the spec 05 3-part contract, no execute.
2. ``tool.input_model.model_validate(input)`` -- a ``ValidationError``
   returns a typed-error result with the 3-part contract, no execute.
3. Per-call ``is_read_only`` is computed before execute so an injected
   observer (heartbeat / sidecar) can record the worst-case mutation
   profile of *this specific invocation* rather than the tool's class-
   level worst case.
4. ``tool.execute`` runs under ``declaration.timeout_seconds`` via
   ``asyncio.timeout`` over an explicit task. A deadline expiry (the task
   is cancelled) becomes a typed-error result with the 3-part contract; a
   ``TimeoutError`` *raised by the tool itself* (the task completes with
   that exception) is a genuine tool exception and propagates unchanged --
   it is not turned into a synthetic timeout result. The tool's own
   ``ctx.run_subprocess`` already handles inner-subprocess timeouts; this
   guard is the outer safety net.
5. ``ToolResult.content`` exceeding the spec 04 inline budget is routed
   through ``services.tool_outputs.offload_tool_output``; the caller sees
   an inline preview + ref token and the full payload lands in scratch.
6. Exceptions raised by ``tool.execute`` are NOT caught here. The engine
   loop in ``_loop.run_query`` already converts them to a generic non-
   revealing transcript marker; catching here would leak ``type(exc)`` or
   ``exc.args`` into the model's view of history.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.services.tool_outputs import offload_tool_output
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry

_PermissionGate = Callable[[PermissionRequest], PermissionDecision]


@dataclass(frozen=True)
class DispatchRecord:
    """One-line audit of a dispatch call surfaced to ``on_dispatch``.

    ``is_read_only`` reflects the *per-call* refinement
    (``tool.is_read_only_for(input)``), not the class-level worst case.
    Unknown-tool dispatches record ``is_read_only=True`` because no
    side-effecting work ran.
    """

    tool_name: str
    is_read_only: bool
    is_error: bool
    elapsed_seconds: float
    offloaded: bool


_DispatchObserver = Callable[[DispatchRecord], None]


@dataclass
class EngineToolDispatcher:
    """``ToolDispatcher`` (``_loop.py``) implemented over a ``ToolRegistry``.

    Construction is wired by the harness (slice D); test fixtures construct
    one per-test with a ``tmp_path`` working_dir and optional scratch_dir.
    """

    registry: ToolRegistry
    working_dir: Path
    session_id: str
    scratch_dir: Path | None = None
    on_dispatch: _DispatchObserver | None = None
    # Optional permission gate (Spec 13C): runs before execute. ``None`` means
    # no gating, so existing call sites are unaffected.
    permission_gate: _PermissionGate | None = None
    # Opaque per-session metadata merged into every ToolExecutionContext. Keeps
    # the engine skill-agnostic: the skills layer stuffs its SkillContext here
    # under its own key and the skill tool reads it back (Spec 06 slice 2).
    context_metadata: dict[str, Any] = field(default_factory=dict)

    async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
        tool = self.registry.get(name)
        if tool is None:
            return self._unknown(name)

        try:
            tool.input_model.model_validate(input)
        except ValidationError as exc:
            return self._schema_invalid(tool_name=name, tool=tool, exc=exc)

        is_read_only = tool.is_read_only_for(input)
        if self.permission_gate is not None:
            effects = tool.effects_for(input)
            request = PermissionRequest(
                tool_name=name,
                is_read_only=is_read_only,
                target_paths=effects.target_paths,
                command=effects.command,
                network_host=effects.network_host,
            )
            decision = self.permission_gate(request)
            if not decision.allowed:
                return self._denied(name, decision, is_read_only)

        ctx = ToolExecutionContext(
            working_dir=self.working_dir,
            session_id=self.session_id,
            scratch_dir=self.scratch_dir,
            metadata=dict(self.context_metadata),  # copy: a tool can't leak into the next call
        )
        timeout = tool.declaration.timeout_seconds
        t0 = time.monotonic()
        # Wrap execution in an explicit task so we can tell *which* call
        # raised ``TimeoutError``: the outer deadline (the task gets
        # cancelled by ``asyncio.timeout``) vs. the tool itself raising
        # ``TimeoutError`` (the task completes with that exception). Only
        # the former maps to a synthetic timeout result; a tool-raised
        # exception propagates unchanged per the dispatch contract (#27).
        exec_task: asyncio.Task[Any] = asyncio.ensure_future(tool.execute(input, ctx))
        try:
            async with asyncio.timeout(timeout):
                result = await exec_task
        except TimeoutError:
            # ``asyncio.timeout`` cancels the inner task on deadline; a
            # completed-but-failed task means the tool raised its own
            # ``TimeoutError``, which is a real tool exception, not a
            # deadline expiry -- re-raise it for the engine loop to handle.
            if exec_task.cancelled():
                elapsed = time.monotonic() - t0
                return self._timeout(
                    tool_name=name, timeout=timeout, is_read_only=is_read_only, elapsed=elapsed
                )
            raise
        elapsed = time.monotonic() - t0

        scratch = self.scratch_dir or (self.working_dir / ".dream" / "scratch")
        inline, pointer = offload_tool_output(
            result.content,
            scratch_dir=scratch,
            tool_use_id=uuid4().hex[:12],
            tool_name=name,
        )
        offloaded = pointer is not None
        self._record(
            name,
            is_read_only=is_read_only,
            is_error=result.is_error,
            elapsed=elapsed,
            offloaded=offloaded,
        )
        return inline, result.is_error

    # --- typed-error builders ------------------------------------------------

    def _unknown(self, name: str) -> tuple[str, bool]:
        known = ", ".join(t.name for t in self.registry.list_tools()) or "<none>"
        content = (
            f"Unknown tool: {name!r}\n"
            f"root_cause: tool name not registered in this session\n"
            f"safe_retry: pick one of: {known}\n"
            f"stop_condition: do not retry with the same unknown name"
        )
        self._record(name, is_read_only=True, is_error=True, elapsed=0.0, offloaded=False)
        return content, True

    def _schema_invalid(
        self, *, tool_name: str, tool: Any, exc: ValidationError
    ) -> tuple[str, bool]:
        content = (
            f"Invalid input for {tool_name}: {exc}\n"
            f"root_cause: input failed pydantic schema validation\n"
            f"safe_retry: re-issue with fields matching the tool input_schema\n"
            f"stop_condition: do not retry the same payload"
        )
        # Pre-execute, so the worst-case class-level read-only flag is the
        # honest answer -- per-call refinement assumes a valid input.
        self._record(
            tool_name,
            is_read_only=tool.is_read_only(),
            is_error=True,
            elapsed=0.0,
            offloaded=False,
        )
        return content, True

    def _denied(
        self, name: str, decision: PermissionDecision, is_read_only: bool
    ) -> tuple[str, bool]:
        if decision.outcome is Outcome.ASK:
            safe_retry = (
                "ask an operator to grant this, or promote the tool in "
                ".harness/tool-tier-overrides.toml"
            )
        else:
            safe_retry = "this action is blocked by the sandbox policy; do not retry as-is"
        content = (
            f"Permission denied for {name!r}: {decision.reason}\n"
            f"root_cause: {decision.rule}\n"
            f"safe_retry: {safe_retry}\n"
            f"stop_condition: do not repeat this call under the current sandbox policy"
        )
        self._record(name, is_read_only=is_read_only, is_error=True, elapsed=0.0, offloaded=False)
        return content, True

    def _timeout(
        self, *, tool_name: str, timeout: float, is_read_only: bool, elapsed: float
    ) -> tuple[str, bool]:
        content = (
            f"Tool {tool_name!r} exceeded its declared timeout of {timeout}s.\n"
            f"root_cause: tool execution exceeded declared timeout_seconds\n"
            f"safe_retry: narrow the request scope or pre-check size before retrying\n"
            f"stop_condition: do not retry beyond the declared tool timeout"
        )
        self._record(
            tool_name,
            is_read_only=is_read_only,
            is_error=True,
            elapsed=elapsed,
            offloaded=False,
        )
        return content, True

    def _record(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        is_error: bool,
        elapsed: float,
        offloaded: bool,
    ) -> None:
        if self.on_dispatch is None:
            return
        self.on_dispatch(
            DispatchRecord(
                tool_name=tool_name,
                is_read_only=is_read_only,
                is_error=is_error,
                elapsed_seconds=elapsed,
                offloaded=offloaded,
            )
        )


__all__ = ["DispatchRecord", "EngineToolDispatcher"]
