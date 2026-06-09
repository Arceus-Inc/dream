"""Default ``task_stop`` tool — terminate a running background task.

Mutating tier-1: the call terminates a real subprocess. Wraps
:meth:`BackgroundTaskManager.stop_task`, which is idempotent for already-
terminal tasks (returns the same record) and raises :class:`ValueError` for
unknown ids or "task exists but no process behind it" — both surface as the
Spec 05 three-part structured error.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._task_context import require_task_context


class TaskStopInput(BaseModel):
    """Arguments for ``task_stop``."""

    task_id: str = Field(description="Task identifier returned by task_create.")


class TaskStopTool(BaseTool):
    """Terminate a running background task."""

    name = "task_stop"
    description = "Stop a running background task. Idempotent for already-terminal tasks."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = TaskStopInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = TaskStopInput.model_validate(input)

        task_ctx = require_task_context(ctx.metadata)
        if isinstance(task_ctx, ToolResult):
            return task_ctx

        try:
            task = await task_ctx.manager.stop_task(args.task_id)
        except ValueError as exc:
            return _err(
                str(exc),
                root_cause=str(exc),
                safe_retry="reuse an id from a prior task_create result, or task_get to confirm it",
                stop_condition="do not retry with the same id",
            )

        return ToolResult(
            content=f"Stopped task {task.id} (status={task.status})",
            metadata={
                "task_id": task.id,
                "status": task.status,
                "return_code": task.return_code,
                "summary": f"stopped task {task.id}",
            },
        )


__all__ = ["TaskStopInput", "TaskStopTool"]
