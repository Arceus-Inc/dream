"""Default ``task_output`` tool — return the log tail for a background task.

Read-only (tier 0, safe). Wraps :meth:`BackgroundTaskManager.read_task_output`
with a tail window so the model can poll a long-running task without blowing
the context budget. ``max_bytes`` is bounded by pydantic (1..100_000) so a
malformed model request can't ask for the whole log.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._session import read_task_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class TaskOutputInput(BaseModel):
    """Arguments for ``task_output``."""

    task_id: str = Field(description="Task identifier returned by task_create.")
    max_bytes: int = Field(
        default=12000,
        ge=1,
        le=100_000,
        description="Tail window in bytes (default 12000, max 100000).",
    )


class TaskOutputTool(BaseTool):
    """Return the last ``max_bytes`` of a background task's output log."""

    name = "task_output"
    description = "Read the last N bytes of a background task's output log."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = TaskOutputInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = TaskOutputInput.model_validate(input)

        task_ctx = read_task_context(ctx.metadata)
        if task_ctx is None:
            return _err(
                "Background tasks are not available in this session.",
                root_cause="no task manager was wired into the execution context",
                safe_retry="run inside a session that enables background tasks",
                stop_condition="do not retry without task wiring",
            )

        try:
            output = task_ctx.manager.read_task_output(
                args.task_id, max_bytes=args.max_bytes
            )
        except ValueError as exc:
            return _err(
                str(exc),
                root_cause=str(exc),
                safe_retry="reuse an id from a prior task_create result, or task_get to confirm it",
                stop_condition="do not retry with the same id",
            )

        return ToolResult(
            content=output if output else "(no output)",
            metadata={
                "task_id": args.task_id,
                "bytes_returned": len(output),
                "summary": f"read {len(output)} bytes from task {args.task_id}",
            },
        )


def _err(content: str, *, root_cause: str, safe_retry: str, stop_condition: str) -> ToolResult:
    return ToolResult(
        content=content,
        is_error=True,
        metadata={
            "root_cause": root_cause,
            "safe_retry": safe_retry,
            "stop_condition": stop_condition,
        },
    )


__all__ = ["TaskOutputInput", "TaskOutputTool"]
