"""Default ``memory_search`` tool — find workspace memory records (spec 11).

Read-only (tier 0, safe): searching never mutates the store, so it needs no
trust promotion. The per-session :class:`~dream.contracts.memory.MemoryStore`
arrives through the ``ToolExecutionContext.metadata`` channel (see
:mod:`dream.memory._context`). The catalogue in the system prompt advertises
what records exist; this tool lets the model pull the right ones in by query.

A missing memory context means memory is not wired in this session — that is a
graceful "no memory available" message, not an error, since memory is advisory.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.memory import MemoryRecord
from dream.contracts.tool import ToolResult
from dream.memory._catalogue import memory_description
from dream.memory._context import read_memory_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

_SNIPPET_MAX = 200


class MemorySearchInput(BaseModel):
    """Arguments for the ``memory_search`` tool."""

    query: str = Field(description="Search terms to match against memory records.")
    limit: int = Field(default=10, ge=1, le=50, description="Max records to return.")


class MemorySearchTool(BaseTool):
    """Search workspace memory and return matching records with snippets."""

    name = "memory_search"
    description = (
        "Search workspace memory for durable facts (conventions, preferences, "
        "decisions) matching a query."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = MemorySearchInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = MemorySearchInput.model_validate(input)
        memory_ctx = read_memory_context(ctx.metadata)
        if memory_ctx is None:
            return ToolResult(
                content="Memory is not available in this session.",
                metadata={"hit_count": 0, "summary": "no memory wired"},
            )

        hits = await memory_ctx.store.search(args.query, limit=args.limit)
        if not hits:
            return ToolResult(
                content=f"No memory records match {args.query!r}.",
                metadata={"hit_count": 0, "summary": "no matches"},
            )

        return ToolResult(
            content="\n\n".join(_render(r) for r in hits),
            metadata={
                "hit_count": len(hits),
                "summary": f"{len(hits)} memory hit(s)",
            },
        )


def _render(record: MemoryRecord) -> str:
    snippet = " ".join(record.content.split())
    if len(snippet) > _SNIPPET_MAX:
        snippet = snippet[: _SNIPPET_MAX - 1].rstrip() + "…"
    return f"- {record.id} — {memory_description(record)}\n  {snippet}"


__all__ = ["MemorySearchInput", "MemorySearchTool"]
