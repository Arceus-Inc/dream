"""Spec 10 slice A — ``EngineToolDispatcher`` honours role-allowed-tools.

When constructed with ``role_allowed_tools``, the dispatcher hard-refuses
any tool name not in the set: no execute, no permission gate, no prompt,
typed error result whose ``root_cause`` is ``tool-not-in-role-manifest``.

This is the capability-minimisation seam (decision #8) — strictly stricter
than the permission gate, and exercised *before* it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.permissions import Outcome, PermissionDecision
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource


class _NoopInput(BaseModel):
    pass


class _ReadTool(BaseTool):
    name = "file_read"
    description = "read"
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _NoopInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="read-ok")


class _WriteTool(BaseTool):
    name = "file_write"
    description = "write"
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
    input_model = _NoopInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="write-ok")


def _registry(*tools: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t, source=ToolSource.DEFAULT)
    return reg


async def test_tool_outside_role_manifest_is_refused_with_typed_error(
    tmp_path: Path,
) -> None:
    reg = _registry(_ReadTool(), _WriteTool())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        role_allowed_tools=frozenset({"file_read"}),
    )

    content, is_error = await disp.dispatch("file_write", {})

    assert is_error is True
    assert "tool-not-in-role-manifest" in content
    # The model must see the manifest-allowed set so it can recover.
    assert "file_read" in content


async def test_tool_outside_role_manifest_does_not_execute(tmp_path: Path) -> None:
    # Substitute a tool that would have observable side effects if executed.
    executed: list[str] = []

    class _Watcher(BaseTool):
        name = "file_write"
        description = "watcher"
        declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
        input_model = _NoopInput

        async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
            executed.append("yes")
            return ToolResult(content="ran")

    reg = _registry(_ReadTool(), _Watcher())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        role_allowed_tools=frozenset({"file_read"}),
    )

    await disp.dispatch("file_write", {})

    assert executed == []


async def test_role_refusal_runs_before_permission_gate(tmp_path: Path) -> None:
    # Even an ALLOW-everything permission gate must not widen the role.
    gate_calls: list[str] = []

    def _allow_all(req):  # type: ignore[no-untyped-def]
        gate_calls.append(req.tool_name)
        return PermissionDecision(outcome=Outcome.ALLOW, reason="ok", rule="test")

    reg = _registry(_ReadTool(), _WriteTool())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        role_allowed_tools=frozenset({"file_read"}),
        permission_gate=_allow_all,
    )

    _, is_error = await disp.dispatch("file_write", {})

    assert is_error is True
    # The gate must not have been consulted — the role refusal short-circuits it.
    assert gate_calls == []


async def test_tool_in_role_manifest_dispatches_normally(tmp_path: Path) -> None:
    reg = _registry(_ReadTool(), _WriteTool())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        role_allowed_tools=frozenset({"file_read"}),
    )

    content, is_error = await disp.dispatch("file_read", {})

    assert is_error is False
    assert content == "read-ok"


async def test_no_role_constraint_means_no_refusal(tmp_path: Path) -> None:
    # Existing call sites that don't pass ``role_allowed_tools`` are unaffected.
    reg = _registry(_ReadTool(), _WriteTool())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
    )

    _, is_error = await disp.dispatch("file_write", {})
    assert is_error is False
