"""Default ``cron_toggle`` tool — enable/disable a cron job (mutating, tier 1).

Ported from OpenHarness ``cron_toggle_tool.py``; flips the ``enabled`` flag on a
registry job via :func:`set_job_enabled`. A disabled job stays in the registry
(so ``cron_list`` still shows it) but the scheduler skips it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._cron import CronJobError, set_job_enabled
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._task_context import require_task_context

_CRON_CONTEXT = {
    "content": "Cron tools are not available in this session.",
    "root_cause": "no task session context was wired",
    "safe_retry": "run inside a session that enables task tools",
}


class CronToggleInput(BaseModel):
    """Arguments for ``cron_toggle``."""

    name: str = Field(description="Cron job name.")
    enabled: bool = Field(description="True to enable, False to disable.")


class CronToggleTool(BaseTool):
    """Enable or disable a cron job by name."""

    name = "cron_toggle"
    description = "Enable or disable a cron job by name."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = CronToggleInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = CronToggleInput.model_validate(input)

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
            found = set_job_enabled(registry, args.name, enabled=args.enabled)
        except (OSError, CronJobError) as exc:
            return _err(
                f"Failed to update the cron registry: {exc}",
                root_cause=str(exc),
                safe_retry="verify the registry path is writable, then retry",
                stop_condition="do not retry until the registry path is corrected",
            )
        if not found:
            return _err(
                f"No cron job named {args.name!r} is registered.",
                root_cause=f"job {args.name!r} not found in cron registry",
                safe_retry="call cron_list to see available job names",
                stop_condition="do not retry with the same name",
            )

        state = "enabled" if args.enabled else "disabled"
        return ToolResult(
            content=f"Cron job {args.name!r} is now {state}.",
            metadata={"name": args.name, "enabled": args.enabled, "summary": f"{state} {args.name}"},
        )


__all__ = ["CronToggleInput", "CronToggleTool"]
