"""``session_search`` — FTS-style search over prior runs (Hermes-inspired).

Search-only: returns slim hits with snippets. No get-by-id drill-down (that
former ``get_run`` companion is intentionally absent). Needs an
:class:`~dream.contracts.episodic.EpisodicStore` via :class:`EpisodicContext`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.episodic import EpisodicSearchHit
from dream.contracts.tool import ToolResult
from dream.memory._episodic_context import read_episodic_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

_SNIPPET_MAX = 240


class SessionSearchInput(BaseModel):
    """Arguments for the ``session_search`` tool."""

    query: str = Field(description="Keywords to match against prior run intent and body.")
    limit: int = Field(default=5, ge=1, le=20, description="Max hits to return.")


class SessionSearchTool(BaseTool):
    """Search prior episodic runs; return slim hits with snippets."""

    name = "session_search"
    description = (
        "Search prior runs/sessions by keyword (intent + narrative). Returns slim "
        "hits with run_id, outcome, and a snippet — use when the user references "
        "past work or you need cross-run context. Search only; no full-run fetch."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=10.0)
    input_model = SessionSearchInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = SessionSearchInput.model_validate(input)
        episodic = read_episodic_context(ctx.metadata)
        if episodic is None:
            return ToolResult(
                content="Episodic store is not available in this session.",
                metadata={"hit_count": 0, "summary": "no episodic store wired"},
            )

        query = args.query.strip()
        if not query:
            return ToolResult(
                content="query must not be empty — pass keywords to search prior runs.",
                is_error=True,
                metadata={
                    "hit_count": 0,
                    "root_cause": "empty query",
                    "safe_retry": "pass a non-empty query string",
                    "stop_condition": "do not retry with an empty query",
                },
            )

        hits = await episodic.store.search(query, limit=args.limit)
        if not hits:
            return ToolResult(
                content=f"No prior runs match {query!r}.",
                metadata={"hit_count": 0, "summary": "no matches"},
            )

        return ToolResult(
            content="\n\n".join(_render(h) for h in hits),
            metadata={
                "hit_count": len(hits),
                "summary": f"{len(hits)} session hit(s)",
                "run_ids": [h.record.run_id for h in hits],
            },
        )


def _render(hit: EpisodicSearchHit) -> str:
    rec = hit.record
    snippet = " ".join((hit.snippet or rec.intent or rec.body).split())
    if len(snippet) > _SNIPPET_MAX:
        snippet = snippet[: _SNIPPET_MAX - 1].rstrip() + "…"
    files = ", ".join(rec.files_touched[:5])
    files_line = f"\n  files: {files}" if files else ""
    return (
        f"- {rec.run_id} — {rec.outcome}"
        f"{f' (task {rec.task_id})' if rec.task_id else ''}\n"
        f"  intent: {rec.intent or '(none)'}\n"
        f"  {snippet}{files_line}"
    )


__all__ = ["SessionSearchInput", "SessionSearchTool"]
