"""Default ``cron_show`` tool — show details for one cron job by name.

Read-only (tier 0, safe). Companion to :class:`CronListTool` — returns the
full record (schedule, timezone, next/last run, tier, description) for the
named job.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._cron import CronJob, CronJobError, get_cron_job
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._render import render_fields
from dream.tools.builtin._task_context import require_task_context


class CronShowInput(BaseModel):
    """Arguments for ``cron_show``."""

    name: str = Field(description="Cron job name (matches the registry's ``name`` field).")


class CronShowTool(BaseTool):
    """Return the full registry record for one cron job."""

    name = "cron_show"
    description = "Show the full configuration of a cron job by name."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = CronShowInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = CronShowInput.model_validate(input)

        task_ctx = require_task_context(
            ctx.metadata,
            content="Cron tools are not available in this session.",
            root_cause="no task session context was wired",
            safe_retry="run inside a session that enables task tools",
        )
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
            job = get_cron_job(registry, args.name)
        except (OSError, CronJobError) as exc:
            # Permission denied, path-is-a-directory, corrupt JSON, transient
            # IO — keep the tool contract recoverable instead of leaking an
            # engine failure.
            return _err(
                f"Failed to read the cron registry: {exc}",
                root_cause=str(exc),
                safe_retry="verify the cron registry path is a readable file, then retry",
                stop_condition="do not retry until the registry path is corrected",
            )
        if job is None:
            return _err(
                f"No cron job named {args.name!r} is registered.",
                root_cause=f"job {args.name!r} not found in cron registry",
                safe_retry="call cron_list to see available job names",
                stop_condition="do not retry with the same name",
            )

        return ToolResult(
            content=_render(job),
            metadata={
                "name": job.name,
                "schedule": job.schedule,
                "enabled": job.enabled,
                "summary": f"cron job {job.name}",
            },
        )


def _render(job: CronJob) -> str:
    # ``timezone``/``description``/``entry_prompt`` keep their truthy skip
    # (``or None`` drops empty strings); ``enabled`` is always shown even when
    # ``False``; datetimes render via ``isoformat`` only when present.
    return render_fields(
        [
            ("name", job.name),
            ("schedule", job.schedule),
            ("enabled", job.enabled),
            ("timezone", job.timezone or None),
            ("description", job.description or None),
            ("tier_required", job.tier_required),
            ("max_session_minutes", job.max_session_minutes),
            ("entry_prompt", job.entry_prompt or None),
            ("next_run", job.next_run.isoformat() if job.next_run is not None else None),
            ("last_run", job.last_run.isoformat() if job.last_run is not None else None),
            ("last_status", job.last_status),
            ("created_at", job.created_at.isoformat() if job.created_at is not None else None),
        ]
    )


__all__ = ["CronShowInput", "CronShowTool"]
