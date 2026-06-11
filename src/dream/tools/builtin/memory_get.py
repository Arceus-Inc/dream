"""Default ``memory_get`` tool — load one workspace memory record (spec 11).

Read-only (tier 0, safe): reading a record never mutates anything, so it needs
no trust promotion. The per-session :class:`~dream.contracts.memory.MemoryStore`
arrives through the ``ToolExecutionContext.metadata`` channel (see
:mod:`dream.memory._context`). This is the load step of progressive disclosure:
the catalogue / ``memory_search`` surface ids, and this pulls the full body in.

A missing record is the caller's mistake (a stale id), so it returns the Spec 05
three-part error contract pointing the model back at search rather than a silent
empty result. A missing memory context is a graceful "no memory" message.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.memory._context import read_memory_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err


class MemoryGetInput(BaseModel):
    """Arguments for the ``memory_get`` tool."""

    id: str = Field(description="The id of the memory record to load.")


class MemoryGetTool(BaseTool):
    """Load a memory record's full content by id."""

    name = "memory_get"
    description = "Load the full content of a workspace memory record by its id."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = MemoryGetInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = MemoryGetInput.model_validate(input)
        memory_ctx = read_memory_context(ctx.metadata)
        if memory_ctx is None:
            return ToolResult(
                content="Memory is not available in this session.",
                metadata={"summary": "no memory wired"},
            )

        record = await memory_ctx.store.get(args.id)
        if record is None:
            return _err(
                f"Memory record not found: {args.id}",
                root_cause=f"no memory record has id {args.id!r}",
                safe_retry="use memory_search to find a valid id, then load that",
                stop_condition="do not retry with the same id",
            )

        return ToolResult(
            content=record.content,
            metadata={"id": record.id, "summary": f"loaded memory {record.id!r}"},
        )


__all__ = ["MemoryGetInput", "MemoryGetTool"]
