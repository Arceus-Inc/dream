"""spawn_subagent tool — delegate a scoped subtask to a child agent.

The child's final message is returned verbatim to the parent as the tool
result. This is the inline (blocking) mode: the parent's tool call blocks
until the child session finishes. Background mode is a future addition.

Design decisions:
- Failure-as-data: a child error returns status="failed" with is_error=False
  so the parent model sees the failure and can adapt rather than having its
  own turn aborted.
- Cap enforcement: MAX_SPAWNS_PER_SESSION (16) hard stops the 17th call
  with a structured three-part error.
- Unknown tool names: reported in structured["unknown_tools"] rather than
  silently dropped; the spawn closure detects them.
- Context miss: graceful three-part error when no SpawnContext is wired
  (child session, spawn=False).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.spawn._context import read_spawn_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err


class SpawnSubagentInput(BaseModel):
    """Input schema for the spawn_subagent tool."""

    task: str = Field(description="The task to delegate to the child agent.")
    tools: list[str] | None = Field(
        default=None,
        description=(
            "Tool names the child may use. None inherits everything permitted "
            "by the parent session's sandbox tier."
        ),
    )
    model: str | None = Field(
        default=None,
        description="Model for the child session. None uses the parent's model.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Maximum turns for the child session. None uses the harness default.",
    )


class SpawnSubagentTool(BaseTool):
    """Delegate a scoped subtask to a child agent session.

    The child runs inline (blocking the parent turn) and its final message
    is returned as the tool result. The child cannot spawn further children
    (depth-1 leaf constraint enforced by removing this tool from the child's
    allowlist via the synthesized manifest's ``disallowed_tools``).
    """

    name = "spawn_subagent"
    description = (
        "Delegate a scoped subtask to a child agent session. "
        "The child's final message is returned verbatim as the tool result — "
        "make the task self-contained so the child can deliver a complete answer. "
        "Children cannot spawn further children."
    )
    declaration = ToolDeclaration(
        risk="mutating",
        tier_required=1,
        timeout_seconds=600.0,
    )
    input_model = SpawnSubagentInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = SpawnSubagentInput.model_validate(input)

        spawn_ctx = read_spawn_context(ctx.metadata)
        if spawn_ctx is None:
            return _err(
                "Spawning a subagent is not available in this session.",
                root_cause=(
                    "no spawn context was wired into the execution context; "
                    "this session is either a child session (depth-1 leaf) "
                    "or spawn was disabled at harness build time"
                ),
                safe_retry="run inside a session that enables spawn (spawn=True, non-child session)",
                stop_condition="do not retry spawning in this session",
            )

        if not spawn_ctx.budget.acquire():
            return _err(
                f"Spawn cap reached ({spawn_ctx.budget.cap} children already spawned).",
                root_cause="spawn cap reached",
                safe_retry="consolidate remaining work into fewer subtasks",
                stop_condition="do not spawn again this session",
            )

        try:
            outcome = await spawn_ctx.spawn(
                args.task,
                args.tools,
                args.model,
                args.max_turns,
            )
        except Exception as exc:
            # Child failure is data, not an exception: parent turn continues.
            return ToolResult(
                content=f"Child session failed: {exc}",
                is_error=False,
                structured={
                    "status": "failed",
                    "child_session_id": None,
                    "cost_usd": None,
                    "error": str(exc),
                },
            )

        structured: dict[str, Any] = {
            "status": outcome.status,
            "child_session_id": outcome.session_id,
            "cost_usd": getattr(outcome.cost, "cost_usd", None),
        }
        if outcome.unknown_tools:
            structured["unknown_tools"] = outcome.unknown_tools

        return ToolResult(
            content=outcome.final_text,
            is_error=False,
            structured=structured,
        )


__all__ = ["SpawnSubagentInput", "SpawnSubagentTool"]
