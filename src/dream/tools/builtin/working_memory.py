"""Task-memory tools — the agent's working-memory scratchpad (spec 11a).

Three safe, tier-0 tools over the per-session
:class:`~dream.memory._working.WorkingMemory`, reached through the
``ToolExecutionContext.metadata`` channel via a
:class:`~dream.memory._task_context.TaskMemoryContext` (see
:mod:`dream.memory._task_context`).

These are deliberately **safe / tier 0** even though write/append mutate a file:
working memory is the agent's own cognition under the worktree sidecar — never
the repo working tree, the network, or the durable store — so the sandbox tier
must not gate it. The agent can always journal, even under a read-only repo tier.
A missing task-memory context degrades gracefully (a "not available" message),
mirroring the read-store tools.

Past the 50 KB cap the write/append tools flag a ``warning`` so the agent can
self-compress (read → summarise → write back); automatic runtime-driven
compression lives in :meth:`WorkingMemory.maybe_compress`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.memory._task_context import read_task_memory_context
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

_UNAVAILABLE = "Task memory is not available in this session."


class WorkingMemoryReadInput(BaseModel):
    """Arguments for the ``working_memory_read`` tool (none)."""


class WorkingMemoryWriteInput(BaseModel):
    """Arguments for the ``working_memory_write`` tool."""

    content: str = Field(description="Full replacement contents for working memory.")


class WorkingMemoryAppendInput(BaseModel):
    """Arguments for the ``working_memory_append`` tool."""

    note: str = Field(description="A note to append to working memory, on its own line.")


class WorkingMemoryReadTool(BaseTool):
    """Read the current task working-memory scratchpad."""

    name = "working_memory_read"
    description = (
        "Read your task working memory — the free-form scratchpad for what you "
        "figured out, open questions, and things to remember later in this task."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = WorkingMemoryReadInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        WorkingMemoryReadInput.model_validate(input)
        task_ctx = read_task_memory_context(ctx.metadata)
        if task_ctx is None:
            return ToolResult(content=_UNAVAILABLE, metadata={"summary": "no task memory wired"})

        content = task_ctx.working_memory.read()
        if not content:
            return ToolResult(
                content="Working memory is empty.",
                metadata={"summary": "working memory empty"},
            )
        return ToolResult(
            content=content,
            metadata={"summary": f"read {len(content.encode('utf-8'))} bytes"},
        )


class WorkingMemoryWriteTool(BaseTool):
    """Replace the task working-memory scratchpad."""

    name = "working_memory_write"
    description = "Replace your task working memory with new contents."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = WorkingMemoryWriteInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = WorkingMemoryWriteInput.model_validate(input)
        task_ctx = read_task_memory_context(ctx.metadata)
        if task_ctx is None:
            return ToolResult(content=_UNAVAILABLE, metadata={"summary": "no task memory wired"})

        wm = task_ctx.working_memory
        wm.write(args.content)
        return _wrote_result(wm.size_bytes(), wm.over_cap(), wm.cap_bytes)


class WorkingMemoryAppendTool(BaseTool):
    """Append a note to the task working-memory scratchpad."""

    name = "working_memory_append"
    description = "Append a note to your task working memory, on its own line."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = WorkingMemoryAppendInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = WorkingMemoryAppendInput.model_validate(input)
        task_ctx = read_task_memory_context(ctx.metadata)
        if task_ctx is None:
            return ToolResult(content=_UNAVAILABLE, metadata={"summary": "no task memory wired"})

        wm = task_ctx.working_memory
        wm.append(args.note)
        return _wrote_result(wm.size_bytes(), wm.over_cap(), wm.cap_bytes)


def _wrote_result(size: int, over_cap: bool, cap: int) -> ToolResult:
    metadata: dict[str, Any] = {"bytes_written": size, "summary": f"working memory is {size} bytes"}
    if over_cap:
        metadata["warning"] = True
        content = (
            f"Wrote working memory ({size} bytes), over the {cap}-byte cap — "
            "summarise and rewrite it to stay under the cap."
        )
    else:
        content = f"Wrote working memory ({size} bytes)."
    return ToolResult(content=content, metadata=metadata)


__all__ = [
    "WorkingMemoryAppendInput",
    "WorkingMemoryAppendTool",
    "WorkingMemoryReadInput",
    "WorkingMemoryReadTool",
    "WorkingMemoryWriteInput",
    "WorkingMemoryWriteTool",
]
