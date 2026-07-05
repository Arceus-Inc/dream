"""Default ``remote_trigger`` tool — fire a cron kind now (mutating, tier 1).

Ported from OpenHarness ``remote_trigger_tool.py`` and adapted to dream's cron
model. dream crons are *agent-turn* triggers, not shell commands, so "run now"
routes through :func:`dream.services.cron.run_cron_kind` — the same entrypoint
the scheduler uses: it loads the ``.harness/cron/{kind}.toml`` manifest, spawns
the cron session via the session's task manager, and advances the registry's
``next_run``. Returns the spawned task id (the run continues in the background).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.services.cron import run_cron_kind
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._task_context import require_task_context

_CRON_CONTEXT = {
    "content": "Cron tools are not available in this session.",
    "root_cause": "no task session context was wired",
    "safe_retry": "run inside a session that enables task tools",
}


class RemoteTriggerInput(BaseModel):
    """Arguments for ``remote_trigger``."""

    name: str = Field(description="Cron kind to fire now (matches the manifest/registry name).")


class RemoteTriggerTool(BaseTool):
    """Fire a configured cron kind immediately via the scheduler's run path."""

    name = "remote_trigger"
    description = "Trigger a configured cron job to run immediately."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=30.0)
    input_model = RemoteTriggerInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = RemoteTriggerInput.model_validate(input)

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
            record = await run_cron_kind(
                kind=args.name,
                working_dir=ctx.working_dir,
                manager=task_ctx.manager,
                registry_path=registry,
            )
        except FileNotFoundError as exc:
            return _err(
                f"No cron manifest for {args.name!r}.",
                root_cause=str(exc),
                safe_retry="create .harness/cron/{name}.toml, then retry",
                stop_condition="do not retry until the manifest exists",
            )

        if record is None:
            return ToolResult(
                content=f"Cron kind {args.name!r} is disabled; nothing was fired.",
                metadata={"name": args.name, "fired": False, "summary": "skipped (disabled)"},
            )
        return ToolResult(
            content=f"Triggered cron kind {args.name!r}; running as task {record.id}.",
            metadata={
                "name": args.name,
                "fired": True,
                "task_id": record.id,
                "summary": f"triggered {args.name}",
            },
        )


__all__ = ["RemoteTriggerInput", "RemoteTriggerTool"]
