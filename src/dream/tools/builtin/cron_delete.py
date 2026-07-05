"""Default ``cron_delete`` tool — remove a cron job by name (mutating, tier 1).

Ported from OpenHarness ``cron_delete_tool.py``; deletes from the session's cron
registry via :func:`delete_cron_job`. A missing job is a recoverable "not found"
error, not a crash.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._cron import CronJobError, delete_cron_job
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._task_context import require_task_context

_CRON_CONTEXT = {
    "content": "Cron tools are not available in this session.",
    "root_cause": "no task session context was wired",
    "safe_retry": "run inside a session that enables task tools",
}


class CronDeleteInput(BaseModel):
    """Arguments for ``cron_delete``."""

    name: str = Field(description="Cron job name to delete.")


class CronDeleteTool(BaseTool):
    """Delete a cron job from the session registry."""

    name = "cron_delete"
    description = "Delete a cron job by name."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = CronDeleteInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = CronDeleteInput.model_validate(input)

        task_ctx = require_task_context(ctx.metadata, **_CRON_CONTEXT)
        if isinstance(task_ctx, ToolResult):
            return task_ctx
        registry = task_ctx.cron_registry_path
        if registry is None:
            return _err(
                "No cron registry path is configured for this session.",
                root_cause="task session context has no cron_registry_path",
                safe_retry="wire cron_registry_path into the TaskSessionContext",
                stop_condition="do not retry until cron is enabled in this session",
            )

        try:
            removed = delete_cron_job(registry, args.name)
        except (OSError, CronJobError) as exc:
            return _err(
                f"Failed to update the cron registry: {exc}",
                root_cause=str(exc),
                safe_retry="verify the registry path is writable, then retry",
                stop_condition="do not retry until the registry path is corrected",
            )
        if not removed:
            return _err(
                f"No cron job named {args.name!r} is registered.",
                root_cause=f"job {args.name!r} not found in cron registry",
                safe_retry="call cron_list to see available job names",
                stop_condition="do not retry with the same name",
            )

        return ToolResult(
            content=f"Deleted cron job {args.name!r}.",
            metadata={"name": args.name, "summary": f"deleted cron job {args.name}"},
        )


__all__ = ["CronDeleteInput", "CronDeleteTool"]
