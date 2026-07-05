"""Default ``task_update`` tool — update a background task's metadata (tier 1).

Ported from OpenHarness ``task_update_tool.py``. Completes the task surface
(create / get / output / stop) with an in-place metadata update:
``description``, ``progress`` (0-100), and a short ``status_note`` for progress
tracking. Progress and note ride the record's ``metadata`` map; the manager
rebinds its canonical record (records are frozen).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._task_context import require_task_context


class TaskUpdateInput(BaseModel):
    """Arguments for ``task_update``."""

    task_id: str = Field(description="Task identifier returned by task_create.")
    description: str | None = Field(default=None, description="Updated task description.")
    progress: int | None = Field(default=None, ge=0, le=100, description="Progress percentage.")
    status_note: str | None = Field(default=None, description="Short human-readable status note.")

    @model_validator(mode="after")
    def _at_least_one(self) -> TaskUpdateInput:
        if self.description is None and self.progress is None and self.status_note is None:
            raise ValueError("provide at least one of description, progress, or status_note")
        return self


class TaskUpdateTool(BaseTool):
    """Update a task's description, progress, or status note."""

    name = "task_update"
    description = "Update a background task's description, progress, or status note."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
    input_model = TaskUpdateInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = TaskUpdateInput.model_validate(input)

        task_ctx = require_task_context(ctx.metadata)
        if isinstance(task_ctx, ToolResult):
            return task_ctx

        if task_ctx.manager.get_task(args.task_id) is None:
            return _err(
                f"No task found with id: {args.task_id}",
                root_cause=f"task id {args.task_id!r} is not in the manager's table",
                safe_retry="call task_create first, or check the id from its result",
                stop_condition="do not retry with the same unknown id",
            )

        task = task_ctx.manager.update_task(
            args.task_id,
            description=args.description,
            progress=args.progress,
            status_note=args.status_note,
        )

        parts = [f"Updated task {task.id}."]
        if args.description is not None:
            parts.append(f"description={task.description!r}")
        if args.progress is not None:
            parts.append(f"progress={task.metadata.get('progress', '')}%")
        if args.status_note is not None:
            parts.append(f"note={task.metadata.get('status_note', '')!r}")
        return ToolResult(
            content=" ".join(parts),
            metadata={"task_id": task.id, "summary": f"updated task {task.id}"},
        )


__all__ = ["TaskUpdateInput", "TaskUpdateTool"]
