"""``delegation_get`` — poll a background ``spawn_subagent`` handle.

OpenHarness ``task_output`` / Hermes lifecycle ``status`` adapted to Dream's
``AsyncDelegationManager``. Only useful after ``background=true``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class DelegationGetInput(BaseModel):
    delegation_id: str = Field(description="Id returned by background spawn_subagent.")


class DelegationGetTool(BaseTool):
    name = "delegation_get"
    description = (
        "Poll a background spawn_subagent delegation by id. "
        "Returns status and any completed summary."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = DelegationGetInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = DelegationGetInput.model_validate(input)
        if ctx.delegations is None:
            return ToolResult(
                content="No async delegation manager on this session.",
                is_error=True,
            )
        snap = ctx.delegations.get(args.delegation_id)
        if snap is None:
            return ToolResult(
                content=f"Unknown delegation_id {args.delegation_id!r}.",
                is_error=True,
            )
        lines = [
            f"delegation_id={snap.delegation_id}",
            f"status={snap.status.value}",
            f"subagents={','.join(snap.subagent_names)}",
        ]
        if snap.error:
            lines.append(f"error={snap.error}")
        for result in snap.results:
            state = "ok" if result.success else "failed"
            body = result.output if result.success else (result.error or "")
            lines.append(f"- {result.name}: {state}\n{body}")
        return ToolResult(
            content="\n".join(lines),
            metadata={
                "delegation_id": snap.delegation_id,
                "status": snap.status.value,
                "summary": f"delegation {snap.delegation_id} is {snap.status.value}",
            },
        )


__all__ = ["DelegationGetInput", "DelegationGetTool"]
