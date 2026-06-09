"""Default ``plan_show`` tool — show an exec-plan by task id.

Read-only (tier 0, safe). When ``state`` is given, looks up the plan under
``{plans_root}/{state}/``; otherwise searches the four FSM states in
:data:`PLAN_STATES` order (``draft → active → completed → archived``),
returning the first hit. The found state is surfaced in
``metadata["state"]`` so the caller knows which lifecycle bucket the plan
came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.config.paths import _checked_task_id
from dream.contracts.tool import ToolResult
from dream.tasks._fsm import PLAN_STATES, plan_dir
from dream.tasks._ledger import LedgerState
from dream.tasks._plan import ExecPlan, read_plan
from dream.tasks._session import read_task_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class PlanShowInput(BaseModel):
    """Arguments for ``plan_show``."""

    task_id: str = Field(description="Exec-plan task id (e.g. 2024-01-01-feat).")
    state: str | None = Field(
        default=None,
        description=(
            "Optional FSM state (draft/active/completed/archived). "
            "If omitted, all states are searched in lifecycle order."
        ),
    )


class PlanShowTool(BaseTool):
    """Show the rendered Markdown for an exec-plan by task id."""

    name = "plan_show"
    description = "Show the rendered Markdown for an exec-plan by task id."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = PlanShowInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = PlanShowInput.model_validate(input)

        # Reject empty / traversal-like ids up front so a bad id is a normal
        # tool error rather than a ValueError escaping from read_plan deeper in.
        try:
            _checked_task_id(args.task_id)
        except ValueError as exc:
            return _err(
                f"Invalid task_id: {args.task_id!r}.",
                root_cause=str(exc),
                safe_retry="pass a task_id with no path separators or traversal",
                stop_condition="do not retry with the same task_id",
            )

        task_ctx = read_task_context(ctx.metadata)
        if task_ctx is None:
            return _err(
                "Plan tools are not available in this session.",
                root_cause="no task session context was wired",
                safe_retry="run inside a session that enables task tools",
                stop_condition="do not retry without task wiring",
            )

        plans_root = task_ctx.plans_root
        if plans_root is None:
            return _err(
                "No exec-plan root is configured for this session.",
                root_cause="task session context has no plans_root",
                safe_retry="wire plans_root into the TaskSessionContext",
                stop_condition="do not retry until plans are enabled in this session",
            )

        if args.state is not None:
            if args.state not in PLAN_STATES:
                return _err(
                    f"Unknown plan state: {args.state!r}.",
                    root_cause=f"{args.state!r} is not one of {list(PLAN_STATES)}",
                    safe_retry="use one of draft, active, completed, archived",
                    stop_condition="do not retry with this state value",
                )
            found = _load(plans_root, args.state, args.task_id)
            if found is None:
                return _err(
                    f"No exec-plan {args.task_id!r} in state {args.state!r}.",
                    root_cause=f"plan not found under {plans_root}/{args.state}",
                    safe_retry="try a different state or omit state to search all",
                    stop_condition="do not retry with the same (task_id, state)",
                )
            plan, state = found, args.state
        else:
            for candidate in PLAN_STATES:
                hit = _load(plans_root, candidate, args.task_id)
                if hit is not None:
                    plan, state = hit, candidate
                    break
            else:
                return _err(
                    f"No exec-plan found for task_id {args.task_id!r}.",
                    root_cause=f"plan not found under any state in {plans_root}",
                    safe_retry="check the task_id; call cron_list/task tools to discover ids",
                    stop_condition="do not retry with the same task_id",
                )

        return ToolResult(
            content=plan.to_markdown(),
            metadata={
                "task_id": plan.task_id,
                "state": state,
                "summary": f"exec-plan {plan.task_id} ({state})",
            },
        )


def _load(plans_root: Path, state: LedgerState, task_id: str) -> ExecPlan | None:
    """Return the plan in ``state`` for ``task_id``, or ``None`` if missing.

    All other exceptions (e.g. ledger/markdown mismatch, traversal id) are
    let through so a corrupt plan surfaces as an engine-level error rather
    than a misleading "not found".
    """
    try:
        return read_plan(plan_dir(plans_root, state=state), task_id=task_id)
    except FileNotFoundError:
        return None


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


__all__ = ["PlanShowInput", "PlanShowTool"]
