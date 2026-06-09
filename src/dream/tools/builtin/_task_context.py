"""Shared guard: pull a :class:`TaskSessionContext` out of tool metadata.

The ``task_*`` / ``cron_*`` / ``plan_*`` tools all begin by reading the
per-session task wiring from ``ctx.metadata`` and, when it is absent, returning
the same-shaped Spec 05 "not available in this session" error. This collapses
that copy-pasted guard into one call: a present context is returned for the
caller to use; an absent one becomes the ready-to-return ``ToolResult``.

The exact wording differs by tool family (``task_*`` speaks of "Background
tasks" / "task manager"; ``cron_*`` / ``plan_*`` speak of "Cron tools" /
"task session context"), so the message strings are parameters with the
``task_*`` family as the defaults.
"""

from __future__ import annotations

from dream.contracts.tool import ToolResult
from dream.tasks._session import TaskSessionContext, read_task_context
from dream.tools.builtin._errors import tool_error


def require_task_context(
    metadata: dict[str, object],
    *,
    content: str = "Background tasks are not available in this session.",
    root_cause: str = "no task manager was wired into the execution context",
    safe_retry: str = "run inside a session that enables background tasks",
    stop_condition: str = "do not retry without task wiring",
) -> TaskSessionContext | ToolResult:
    """Return the session's :class:`TaskSessionContext`, or the absence error.

    Defaults reproduce the ``task_*`` tools' wording verbatim; ``cron_*`` /
    ``plan_*`` pass their own ``content`` / ``root_cause`` / ``safe_retry`` so
    each call site's message is unchanged.
    """
    task_ctx = read_task_context(metadata)
    if task_ctx is not None:
        return task_ctx
    return tool_error(
        content,
        root_cause=root_cause,
        safe_retry=safe_retry,
        stop_condition=stop_condition,
    )


__all__ = ["require_task_context"]
