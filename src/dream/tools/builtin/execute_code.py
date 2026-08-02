"""``execute_code`` — Hermes-style mechanical multi-step collapse (SOTA #10).

The model writes one Python script. The script RPCs allowlisted Dream tools.
Only stdout + metadata return as the parent tool result; nested tool I/O never
becomes parent conversation messages.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry
from dream.tools.execute_code import (
    EXECUTE_CODE_REGISTRY_KEY,
    ExecuteCodeStatus,
    NestedToolName,
    RegistryToolInvoker,
    sandbox_tools_for,
)
from dream.tools.execute_code import _session as session_mod
from dream.tools.execute_code._session import run_execute_code_session

# Same key as spawn_subagent.PARENT_TOOLS_KEY — avoid importing that module here.
_PARENT_TOOLS_KEY = "dream.parent_tools"


class ExecuteCodeInput(BaseModel):
    """Arguments for ``execute_code``."""

    code: str = Field(description="Python source to run in the sandbox with dream_tools RPC.")


class ExecuteCodeTool(BaseTool):
    """Collapse multi-step tool chains into one parent observation."""

    name = "execute_code"
    description = (
        "Run a Python script that calls allowlisted tools via `from dream_tools import ...`. "
        "Use for mechanical multi-step sequences (read→transform→write, gather then summarize) "
        "so intermediate tool I/O does not fill the conversation. Only script stdout returns. "
        "Available: read_file, write_file, edit_file, grep, glob, bash, web_search, web_extract."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=300.0)
    input_model = ExecuteCodeInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = ExecuteCodeInput.model_validate(input)
        if not args.code.strip():
            return _refused("No code provided.", tool_calls_made=0)

        registry = ctx.metadata.get(EXECUTE_CODE_REGISTRY_KEY)
        if not isinstance(registry, ToolRegistry):
            return _refused(
                "execute_code invoker unavailable: tool registry missing from session context.",
                tool_calls_made=0,
            )

        session_names = frozenset(tool.name for tool in registry.list_tools())
        parent_tools = ctx.metadata.get(_PARENT_TOOLS_KEY)
        if isinstance(parent_tools, (set, frozenset, list, tuple)):
            session_names &= frozenset(str(name) for name in parent_tools)

        allowed = sandbox_tools_for(session_names)
        if not allowed:
            return _refused(
                "execute_code refused: no sandbox tools available "
                "(session ∩ allowlist is empty; fail-closed).",
                tool_calls_made=0,
            )

        invoker = RegistryToolInvoker(
            registry=registry,
            context=ctx,
            allowed=allowed,
            max_calls=session_mod.DEFAULT_MAX_TOOL_CALLS,
        )
        outcome = await run_execute_code_session(
            code=args.code,
            working_dir=ctx.working_dir,
            allowed=allowed,
            invoker=invoker,
            timeout_seconds=self.declaration.timeout_seconds,
            max_tool_calls=session_mod.DEFAULT_MAX_TOOL_CALLS,
        )

        structured = outcome.model_dump(mode="json")
        is_error = outcome.status is not ExecuteCodeStatus.SUCCESS
        return ToolResult(
            content=outcome.output if outcome.output else outcome.status.value,
            structured=structured,
            is_error=is_error,
            metadata={
                "status": outcome.status.value,
                "tool_calls_made": outcome.tool_calls_made,
                "exit_code": outcome.exit_code,
                "duration_seconds": outcome.duration_seconds,
                "allowed_tools": sorted(t.value for t in allowed),
            },
        )


def _refused(message: str, *, tool_calls_made: int) -> ToolResult:
    structured = {
        "status": ExecuteCodeStatus.REFUSED.value,
        "output": message,
        "exit_code": 1,
        "tool_calls_made": tool_calls_made,
        "duration_seconds": 0.0,
        "stderr": "",
    }
    return ToolResult(
        content=message,
        structured=structured,
        is_error=True,
        metadata={
            "status": ExecuteCodeStatus.REFUSED.value,
            "tool_calls_made": tool_calls_made,
            "exit_code": 1,
        },
    )


__all__ = ["ExecuteCodeInput", "ExecuteCodeTool", "NestedToolName"]
