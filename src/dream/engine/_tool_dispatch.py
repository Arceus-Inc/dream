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

from dream.contracts.hook import HookEvent
from dream.hooks import HookExecutor
from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.services.tool_outputs import offload_tool_output
from dream.tools._base import BaseTool
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry

PermissionGate = Callable[[PermissionRequest], PermissionDecision]
"""A pure decision function the dispatcher consults before executing a tool."""

# Spec 13: POST_TOOL_USE carries a bounded ``result_summary`` so an observer
# never gets the full (possibly offloaded) payload through the hook channel.
_RESULT_SUMMARY_MAX_CHARS = 500


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
    permission_gate: PermissionGate | None = None
    # Optional capability-minimisation set (Spec 10 decision #8). When set, the
    # dispatcher hard-refuses any tool name not in the set *before* the
    # permission gate -- a role cannot widen itself even with an allow-all gate.
    # ``None`` means "no role constraint", so existing call sites are unaffected.
    role_allowed_tools: frozenset[str] | None = None
    # Opaque per-session metadata merged into every ToolExecutionContext. Keeps
    # the engine skill-agnostic: the skills layer stuffs its SkillContext here
    # under its own key and the skill tool reads it back (Spec 06 slice 2).
    context_metadata: dict[str, Any] = field(default_factory=dict)
    # Optional spec-13 lifecycle hook executor. When set, PRE_TOOL_USE fires
    # immediately before the dispatch atom and POST_TOOL_USE immediately after
    # the ``(content, is_error)`` result exists -- observer-only (hooks never
    # veto), so the call proceeds regardless. ``None`` means no firing, so
    # existing call sites are byte-for-byte unaffected.
    hook_executor: HookExecutor | None = None

    async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
        # Spec 13: PRE_TOOL_USE fires *before* the dispatch atom, then the result
        # is produced, then POST_TOOL_USE fires *after* it exists. The hook is an
        # observer (spec 13 divergence #1: it never vetoes) and ``fire`` never
        # raises, so a faulty hook can't break the loop or split the tool-call
        # atom. Wrapping the whole inner pipeline guarantees POST sees a result
        # for *every* exit (role-refused, unknown, invalid, denied, timeout, ok).
        if self.hook_executor is None:
            return await self._dispatch_inner(name, input)
        await self.hook_executor.fire(
            HookEvent.PRE_TOOL_USE, {"tool_name": name, "tool_input": dict(input)}
        )
        content, is_error = await self._dispatch_inner(name, input)
        await self.hook_executor.fire(
            HookEvent.POST_TOOL_USE,
            {
                "tool_name": name,
                "is_error": is_error,
                "result_summary": content[:_RESULT_SUMMARY_MAX_CHARS],
            },
        )
        return content, is_error

    async def _dispatch_inner(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
        # ``input`` is the raw tool-call argument map straight off the model's
        # ToolUseBlock (e.g. {"path": "src/x.py", "content": "..."}); it is
        # validated against ``tool.input_model`` before any side-effecting work.
        if self.role_allowed_tools is not None and name not in self.role_allowed_tools:
            return self._role_refused(name)

        tool = self.registry.get(name)
        if tool is None:
            return self._unknown(name)

        validation_error = self._validate_input(name, tool, input)
        if validation_error is not None:
            return validation_error

        is_read_only = tool.is_read_only_for(input)
        if self.permission_gate is not None:
            request = self._permission_request(name, tool, input, is_read_only)
            decision = self.permission_gate(request)
            if not decision.allowed:
                return self._denied(name, decision, is_read_only)

        ctx = ToolExecutionContext(
            working_dir=self.working_dir,
            session_id=self.session_id,
            scratch_dir=self.scratch_dir,
            metadata=dict(self.context_metadata),  # copy: a tool can't leak into the next call
        )
        result, elapsed = await self._run_with_timeout(name, tool, input, ctx, is_read_only)
        if isinstance(result, tuple):
            # A synthetic timeout result (already recorded) rather than a ToolResult.
            return result
        return self._offload_and_record(name, result, is_read_only=is_read_only, elapsed=elapsed)

    def _validate_input(
        self, name: str, tool: BaseTool, input: dict[str, Any]
    ) -> tuple[str, bool] | None:
        """Validate ``input`` against the tool's pydantic model.

        Returns a typed-error result on failure (the dispatch contract's 3-part
        envelope, no execute), or ``None`` when the input is well-formed.
        """
        try:
            tool.input_model.model_validate(input)
        except ValidationError as exc:
            return self._schema_invalid(tool_name=name, tool=tool, exc=exc)
        return None

    async def _run_with_timeout(
        self,
        name: str,
        tool: BaseTool,
        input: dict[str, Any],
        ctx: ToolExecutionContext,
        is_read_only: bool,
    ) -> tuple[Any, float]:
        """Execute the tool under its declared timeout.

        Returns ``(result, elapsed)`` on success. On a *deadline* expiry returns
        the synthetic timeout envelope (a ``tuple[str, bool]``, already recorded)
        in the first slot; a ``TimeoutError`` raised by the tool *itself*
        propagates unchanged per the dispatch contract (#27).
        """
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
                return (
                    self._timeout(
                        tool_name=name,
                        timeout=timeout,
                        is_read_only=is_read_only,
                        elapsed=elapsed,
                    ),
                    elapsed,
                )
            raise
        return result, time.monotonic() - t0

    def _offload_and_record(
        self, name: str, result: Any, *, is_read_only: bool, elapsed: float
    ) -> tuple[str, bool]:
        """Offload an oversized payload, emit the dispatch record, return inline."""
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

    def _permission_request(
        self, name: str, tool: BaseTool, input: dict[str, Any], is_read_only: bool
    ) -> PermissionRequest:
        """Build the gate request from the tool's per-call effects.

        Fallback (Spec 13C rollout): a *mutating* tool that reports no path
        or network effect (e.g. ``bash`` running ``pytest -q``,
        ``task_stop``, ``mcp_auth``, and MCP adapters whose side effect is
        process/credential state, not a file) would otherwise produce a
        request the checker cannot classify, fall through to ``ASK``, and be
        denied — breaking those tools under the default policy. We anchor
        such a request to the in-repo working dir so the checker sees a
        WRITE effect: still tier-gated (an untrusted tool is asked, a
        promoted one runs), never silently allowed.
        """
        effects = tool.effects_for(input)
        target_paths = effects.target_paths
        if not is_read_only and not target_paths and effects.network_host is None:
            target_paths = (self.working_dir,)
        return PermissionRequest(
            tool_name=name,
            is_read_only=is_read_only,
            target_paths=target_paths,
            command=effects.command,
            network_host=effects.network_host,
        )

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
        self, *, tool_name: str, tool: BaseTool, exc: ValidationError
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

    def _role_refused(self, name: str) -> tuple[str, bool]:
        allowed = self.role_allowed_tools or frozenset()
        allowed_list = ", ".join(sorted(allowed)) or "<none>"
        content = (
            f"Tool {name!r} is not in this role's manifest.\n"
            f"root_cause: tool-not-in-role-manifest\n"
            f"safe_retry: pick one of the manifest-allowed tools: {allowed_list}\n"
            f"stop_condition: do not request unlisted tools; emit "
            f"request_capability if the role lacks a capability it needs"
        )
        self._record(name, is_read_only=True, is_error=True, elapsed=0.0, offloaded=False)
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


__all__ = ["DispatchRecord", "EngineToolDispatcher", "PermissionGate"]
