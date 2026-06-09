"""Default ``task_get`` tool — return record details for a background task.

Read-only (tier 0, safe). Wraps :meth:`BackgroundTaskManager.get_task` and
surfaces the live record's status, type, return code, and timing through both
a human-readable ``content`` block and a structured ``metadata`` payload so
callers can branch without scraping text.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._session import read_task_context
from dream.tasks._types import TaskRecord
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class TaskGetInput(BaseModel):
    """Arguments for ``task_get``."""

    task_id: str = Field(description="Task identifier returned by task_create.")


class TaskGetTool(BaseTool):
    """Look up a background task by id and return its current state."""

    name = "task_get"
    description = "Return current state for a background task (status, return code, timing)."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = TaskGetInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = TaskGetInput.model_validate(input)

        task_ctx = read_task_context(ctx.metadata)
        if task_ctx is None:
            return _err(
                "Background tasks are not available in this session.",
                root_cause="no task manager was wired into the execution context",
                safe_retry="run inside a session that enables background tasks",
                stop_condition="do not retry without task wiring",
            )

        task = task_ctx.manager.get_task(args.task_id)
        if task is None:
            return _err(
                f"No task found with id: {args.task_id}",
                root_cause=f"task id {args.task_id!r} is not in the manager's table",
                safe_retry="reuse an id from a prior task_create result",
                stop_condition="do not retry with the same id",
            )

        return ToolResult(
            content=_render(task),
            metadata={
                "task_id": task.id,
                "task_type": task.type,
                "status": task.status,
                "return_code": task.return_code,
                "summary": f"{task.type} task {task.id} is {task.status}",
            },
        )


def _render(task: TaskRecord) -> str:
    lines = [
        f"id: {task.id}",
        f"type: {task.type}",
        f"status: {task.status}",
        f"description: {task.description}",
        f"cwd: {task.cwd}",
    ]
    if task.command is not None:
        lines.append(f"command: {task.command}")
    if task.argv is not None:
        lines.append(f"argv: {list(task.argv)}")
    if task.return_code is not None:
        lines.append(f"return_code: {task.return_code}")
    if task.started_at is not None:
        lines.append(f"started_at: {task.started_at}")
    if task.ended_at is not None:
        lines.append(f"ended_at: {task.ended_at}")
    return "\n".join(lines)


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


__all__ = ["TaskGetInput", "TaskGetTool"]
