"""``delegation_stop`` — cancel a background ``spawn_subagent`` handle."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class DelegationStopInput(BaseModel):
    delegation_id: str = Field(description="Id returned by background spawn_subagent.")


class DelegationStopTool(BaseTool):
    name = "delegation_stop"
    description = "Stop a running background spawn_subagent delegation by id."
    declaration = ToolDeclaration(risk="mutating", tier_required=0, timeout_seconds=10.0)
    input_model = DelegationStopInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = DelegationStopInput.model_validate(input)
        if ctx.delegations is None:
            return ToolResult(
                content="No async delegation manager on this session.",
                is_error=True,
            )
        snap = await ctx.delegations.stop(args.delegation_id, session_id=ctx.session_id)
        if snap is None:
            return ToolResult(
                content=f"Unknown delegation_id {args.delegation_id!r}.",
                is_error=True,
            )
        return ToolResult(
            content=f"delegation {snap.delegation_id} status={snap.status.value}",
            metadata={
                "delegation_id": snap.delegation_id,
                "status": snap.status.value,
                "summary": f"stopped delegation {snap.delegation_id}",
            },
        )


__all__ = ["DelegationStopInput", "DelegationStopTool"]
