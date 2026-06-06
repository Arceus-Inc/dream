"""``HeartbeatTool`` — virtual tool the wake runner intercepts structurally.

The tool is real in that it satisfies the ``BaseTool`` contract — name,
description, pydantic input model, declaration — so the provider sees its
schema in the turn's tool list. ``execute`` is functional (validates +
returns the structured payload) but the wake runner typically reads the
tool-use block directly off the assistant turn rather than dispatching;
having ``execute`` work means the tool also composes with a regular
``EngineToolDispatcher`` for paths like the REPL ``/wake`` slash command
that slice 2 will wire up.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

_MAX_TASKS = 5
_MAX_TASK_LEN = 200
_MAX_REASON_LEN = 200


class HeartbeatInput(BaseModel):
    """Arguments the model passes to the ``heartbeat`` tool."""

    action: Literal["skip", "run"] = Field(
        description=(
            "'skip' to defer work, 'run' to start the listed tasks. "
            "Exactly one wake-cycle decision per call."
        ),
    )
    tasks: list[str] = Field(
        default_factory=list,
        max_length=_MAX_TASKS,
        description=(
            "Tasks to queue when action='run'. Ignored when action='skip'. "
            "Max 5 items, each <=200 chars."
        ),
    )
    reason: str = Field(
        max_length=_MAX_REASON_LEN,
        description="One-line justification, <=200 chars.",
    )

    def model_post_init(self, __context: Any) -> None:
        # Per-item length cap; field-level ``max_length`` on ``list[str]`` only
        # bounds the list length, not the strings inside it.
        for t in self.tasks:
            if len(t) > _MAX_TASK_LEN:
                raise ValueError(
                    f"each task must be <= {_MAX_TASK_LEN} chars (got {len(t)})"
                )


class HeartbeatTool(BaseTool):
    """Decide whether to wake the agent for work and what to queue."""

    name = "heartbeat"
    description = (
        "Decide whether to start work this wake cycle. Call EXACTLY ONCE per "
        "background turn. 'skip' defers; 'run' queues the listed tasks."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = HeartbeatInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        del ctx  # the tool is virtual — no I/O, no working_dir use.
        try:
            parsed = HeartbeatInput.model_validate(input)
        except (ValidationError, ValueError) as exc:
            return ToolResult(
                content=f"invalid heartbeat input: {exc}",
                is_error=True,
            )
        # Spec decision: ``tasks`` is meaningless when ``action == "skip"``.
        # Normalize at the boundary so downstream consumers don't re-check.
        tasks = [] if parsed.action == "skip" else list(parsed.tasks)
        payload = {
            "action": parsed.action,
            "tasks": tasks,
            "reason": parsed.reason,
        }
        return ToolResult(
            content=f"heartbeat: {parsed.action}",
            structured=payload,
        )


__all__ = ["HeartbeatInput", "HeartbeatTool"]
