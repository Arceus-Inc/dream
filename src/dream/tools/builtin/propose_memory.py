"""``memory_propose`` tool — nominate a durable memory entry (spec 11a, #4).

The outbound seam: a task may *propose* a durable fact, but it cannot promote it.
This tool validates the slug, writes a ``{ts}-{slug}.md`` proposal to the durable
home queue, and returns. dream never reads, scores, or resolves proposals — that
is lattice's dream phase on its slower clock.

Safe / tier 0: the proposal lands in the dream home, not the repo working tree,
so the sandbox tier does not gate it. A missing task-memory context degrades
gracefully; a malformed slug surfaces the Spec 05 three-part error contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.memory._proposals import InvalidSlugError, write_proposal
from dream.memory._task_context import read_task_memory_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err


class MemoryProposeInput(BaseModel):
    """Arguments for the ``memory_propose`` tool."""

    slug: str = Field(
        description="Unique handle (lowercase letters, digits, hyphens) for the entry."
    )
    content: str = Field(description="The proposed durable-memory entry content.")
    rationale: str = Field(description="One line: why this is worth remembering across tasks.")


class MemoryProposeTool(BaseTool):
    """Propose a durable memory entry for later promotion (outbound only)."""

    name = "memory_propose"
    description = (
        "Propose a durable memory entry (a convention, decision, or fact worth "
        "remembering across tasks). It is queued for review, not applied now."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = MemoryProposeInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = MemoryProposeInput.model_validate(input)
        task_ctx = read_task_memory_context(ctx.metadata)
        if task_ctx is None:
            return ToolResult(
                content="Task memory is not available in this session.",
                metadata={"summary": "no task memory wired"},
            )

        try:
            path = write_proposal(
                task_ctx.proposals_dir,
                slug=args.slug,
                content=args.content,
                rationale=args.rationale,
                source=task_ctx.source_ref,
            )
        except InvalidSlugError as exc:
            return _err(
                f"Invalid proposal slug: {args.slug!r}",
                root_cause=str(exc),
                safe_retry="use a slug of lowercase letters, digits, and hyphens",
                stop_condition="do not retry with the same slug",
            )

        return ToolResult(
            content=f"Proposed durable memory {args.slug!r} for review.",
            metadata={"artifacts": [str(path)], "summary": f"proposed {args.slug!r}"},
        )


__all__ = ["MemoryProposeInput", "MemoryProposeTool"]
