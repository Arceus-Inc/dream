"""Default ``task_create`` tool — spawn a background ``local_bash`` task.

Wraps :meth:`dream.tasks._manager.BackgroundTaskManager.create_shell_task` so
the engine loop can launch a supervised subprocess from a tool call. The
per-session manager is read from ``ctx.metadata`` via
:class:`~dream.tasks._session.TaskSessionContext`; the tool itself holds no
state.

Scope is intentionally narrow this slice:

- Only ``local_bash`` task type. ``local_agent`` requires manager plumbing
  that does not exist yet (no ``create_agent_task``); request that type and
  get a structured error pointing at the limitation.
- The Spec 05 three-part error contract (``root_cause`` / ``safe_retry`` /
  ``stop_condition``) is honoured for every expected failure mode; the tool
  never raises for caller-fault inputs (manager rolls back its own ghost
  state on spawn failure, which we propagate as an error result rather than
  letting the engine see an exception).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tasks._session import read_task_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class TaskCreateInput(BaseModel):
    """Arguments for ``task_create``."""

    description: str = Field(description="Short human-readable description of the task.")
    command: str | None = Field(
        default=None,
        description="Shell-evaluated command string. Mutually exclusive with argv.",
    )
    argv: list[str] | None = Field(
        default=None,
        description="Direct exec vector (bypasses shell). Mutually exclusive with command.",
    )
    cwd: str | None = Field(
        default=None,
        description="Working directory for the subprocess. Defaults to the session cwd.",
    )
    task_type: str = Field(
        default="local_bash",
        description="Task type. Only ``local_bash`` is supported in this slice.",
    )


class TaskCreateTool(BaseTool):
    """Create a background shell task supervised by the harness."""

    name = "task_create"
    description = "Spawn a background local_bash task and return its id and type."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=15.0)
    input_model = TaskCreateInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = TaskCreateInput.model_validate(input)

        task_ctx = read_task_context(ctx.metadata)
        if task_ctx is None:
            return _err(
                "Background tasks are not available in this session.",
                root_cause="no task manager was wired into the execution context",
                safe_retry="run inside a session that enables background tasks",
                stop_condition="do not retry without task wiring",
            )

        if args.command is None and args.argv is None:
            return _err(
                "task_create requires either 'command' or 'argv'.",
                root_cause="neither command nor argv was supplied",
                safe_retry="pass exactly one of 'command' or 'argv'",
                stop_condition="do not retry with both fields empty",
            )
        if args.command is not None and args.argv is not None:
            return _err(
                "task_create accepts only one of 'command' or 'argv'.",
                root_cause="both command and argv were supplied",
                safe_retry="drop whichever is incorrect and call again",
                stop_condition="do not retry with both fields set",
            )

        # The harness only spawns local_bash today. Keeping the input field
        # open (``str``) rather than a closed Literal lets us return the
        # Spec 05 structured error instead of a raw pydantic ValidationError
        # for the common "model picked the wrong task type" case.
        if args.task_type != "local_bash":
            return _err(
                f"Unsupported task_type: {args.task_type!r}.",
                root_cause=f"the harness only supports local_bash, got {args.task_type!r}",
                safe_retry="use task_type='local_bash'",
                stop_condition="do not retry with this task type",
            )

        cwd = args.cwd if args.cwd is not None else str(ctx.working_dir)

        try:
            task = await task_ctx.manager.create_shell_task(
                description=args.description,
                cwd=cwd,
                command=args.command,
                argv=args.argv,
            )
        except ValueError as exc:
            return _err(
                str(exc),
                root_cause=str(exc),
                safe_retry="fix the arguments and call again",
                stop_condition="do not retry with the same arguments",
            )
        except FileNotFoundError as exc:
            return _err(
                f"Failed to spawn task: {exc}",
                root_cause=f"argv[0] not found: {exc}",
                safe_retry="verify the executable is on PATH or use an absolute path",
                stop_condition="do not retry with the same argv",
            )

        return ToolResult(
            content=f"Created task {task.id} ({task.type}): {args.description}",
            metadata={
                "task_id": task.id,
                "task_type": task.type,
                "summary": f"created {task.type} task {task.id}",
            },
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


__all__ = ["TaskCreateInput", "TaskCreateTool"]
