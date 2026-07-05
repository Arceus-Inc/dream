"""Default ``cron_create`` tool — create or replace a cron job (mutating, tier 1).

Completes the cron CRUD surface alongside the read-only ``cron_list`` /
``cron_show``. Ported from OpenHarness ``cron_create_tool.py`` and adapted to
dream's cron model: jobs are *agent-turn* triggers carrying an ``entry_prompt``
(not shell commands / nanobot payloads), written to the session's cron registry
via :func:`upsert_cron_job`, which validates the schedule/timezone and stamps
``next_run``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._cron import CronJob, CronJobError, upsert_cron_job
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._task_context import require_task_context

_CRON_CONTEXT = {
    "content": "Cron tools are not available in this session.",
    "root_cause": "no task session context was wired",
    "safe_retry": "run inside a session that enables task tools",
}


class CronCreateInput(BaseModel):
    """Arguments for ``cron_create``."""

    name: str = Field(description="Unique cron job name.")
    schedule: str = Field(
        description="5-field cron expression, e.g. '*/5 * * * *' or '0 9 * * 1-5'."
    )
    entry_prompt: str | None = Field(
        default=None, description="Instruction the triggered agent turn runs with."
    )
    timezone: str | None = Field(default=None, description="IANA timezone for the schedule.")
    enabled: bool = Field(default=True, description="Whether the job is active.")
    description: str | None = Field(default=None, description="Human-readable note.")
    tier_required: str | None = Field(
        default=None, description="Sandbox tier the triggered session runs at."
    )
    max_session_minutes: int | None = Field(
        default=None, ge=1, description="Hard cap on the triggered session's runtime."
    )


class CronCreateTool(BaseTool):
    """Create or replace a cron job in the session registry."""

    name = "cron_create"
    description = "Create or replace a cron job with a 5-field cron schedule."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = CronCreateInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = CronCreateInput.model_validate(input)

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

        job = CronJob(
            name=args.name,
            schedule=args.schedule,
            timezone=args.timezone,
            enabled=args.enabled,
            description=args.description,
            tier_required=args.tier_required,
            max_session_minutes=args.max_session_minutes,
            entry_prompt=args.entry_prompt,
        )
        try:
            saved = upsert_cron_job(registry, job)
        except CronJobError as exc:
            return _err(
                f"Invalid cron job: {exc}",
                root_cause=str(exc),
                safe_retry="fix the schedule or timezone, then retry",
                stop_condition="do not retry with the same invalid values",
            )

        state = "enabled" if saved.enabled else "disabled"
        next_run = saved.next_run.isoformat() if saved.next_run else "n/a"
        return ToolResult(
            content=f"Saved cron job {saved.name!r} [{saved.schedule}] ({state}); next run {next_run}.",
            metadata={
                "name": saved.name,
                "schedule": saved.schedule,
                "enabled": saved.enabled,
                "summary": f"saved cron job {saved.name}",
            },
        )


__all__ = ["CronCreateInput", "CronCreateTool"]
