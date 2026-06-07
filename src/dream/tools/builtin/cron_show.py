"""Default ``cron_show`` tool — show details for one cron job by name.

Read-only (tier 0, safe). Companion to :class:`CronListTool` — returns the
full record (schedule, timezone, next/last run, tier, description) for the
named job.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._cron import CronJob, get_cron_job
from dream.tasks._session import read_task_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


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

        task_ctx = read_task_context(ctx.metadata)
        if task_ctx is None:
            return _err(
                "Cron tools are not available in this session.",
                root_cause="no task session context was wired",
                safe_retry="run inside a session that enables task tools",
                stop_condition="do not retry without task wiring",
            )

        registry = task_ctx.cron_registry_path
        if registry is None:
            return _err(
                "No cron registry path is configured for this session.",
                root_cause="task session context has no cron_registry_path",
                safe_retry="wire cron_registry_path into the TaskSessionContext",
                stop_condition="do not retry until cron is enabled in this session",
            )

        job = get_cron_job(registry, args.name)
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
    lines = [
        f"name: {job.name}",
        f"schedule: {job.schedule}",
        f"enabled: {job.enabled}",
    ]
    if job.timezone:
        lines.append(f"timezone: {job.timezone}")
    if job.description:
        lines.append(f"description: {job.description}")
    if job.tier_required is not None:
        lines.append(f"tier_required: {job.tier_required}")
    if job.max_session_minutes is not None:
        lines.append(f"max_session_minutes: {job.max_session_minutes}")
    if job.entry_prompt:
        lines.append(f"entry_prompt: {job.entry_prompt}")
    if job.next_run is not None:
        lines.append(f"next_run: {job.next_run.isoformat()}")
    if job.last_run is not None:
        lines.append(f"last_run: {job.last_run.isoformat()}")
    if job.last_status is not None:
        lines.append(f"last_status: {job.last_status}")
    if job.created_at is not None:
        lines.append(f"created_at: {job.created_at.isoformat()}")
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


__all__ = ["CronShowInput", "CronShowTool"]
