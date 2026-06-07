"""Default ``cron_list`` tool — list configured cron jobs.

Read-only (tier 0, safe). The durable registry is at the path the session
plumbed into :class:`TaskSessionContext`; the underlying loader is tolerant
of a missing file (returns ``[]``), so a fresh repo with no cron jobs is a
clean "no jobs" success result rather than an error.

A missing ``cron_registry_path`` on the session, however, is treated as the
caller's setup error (Spec 05 three-part contract): the model gets a
structured error pointing at the wiring gap rather than a silent empty list.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.tasks._cron import CronJob, load_cron_jobs
from dream.tasks._session import read_task_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class CronListInput(BaseModel):
    """``cron_list`` takes no arguments."""


class CronListTool(BaseTool):
    """List configured cron jobs with schedule, enabled state, and next run."""

    name = "cron_list"
    description = "List configured cron jobs with schedule, enabled state, and next run."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = CronListInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        CronListInput.model_validate(input)

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

        jobs = load_cron_jobs(registry)
        if not jobs:
            return ToolResult(
                content="No cron jobs configured.",
                metadata={"job_count": 0, "summary": "no cron jobs"},
            )

        return ToolResult(
            content="\n".join(_render(j) for j in jobs),
            metadata={
                "job_count": len(jobs),
                "summary": f"{len(jobs)} cron job(s)",
            },
        )


def _render(job: CronJob) -> str:
    enabled = "on" if job.enabled else "off"
    tz = f" ({job.timezone})" if job.timezone else ""
    next_run = job.next_run.isoformat() if job.next_run else "n/a"
    last_run = job.last_run.isoformat() if job.last_run else "never"
    last_status = f" ({job.last_status})" if job.last_status else ""
    return (
        f"[{enabled}] {job.name}  {job.schedule}{tz}\n"
        f"     last: {last_run}{last_status}  next: {next_run}"
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


__all__ = ["CronListInput", "CronListTool"]
