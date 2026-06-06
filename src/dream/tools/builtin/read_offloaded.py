"""Default ``read_offloaded`` tool — slice a sidecar-spilled tool output.

Spec 05 slice B. Thin wrapper over
``dream.services.tool_outputs.read_offloaded``. Inputs are resolved against
the session ``scratch_dir`` (not the project working dir) so the model can
only pull back artifacts the engine actually spilled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.services.tool_outputs import read_offloaded
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class ReadOffloadedInput(BaseModel):
    """Arguments for the ``read_offloaded`` tool."""

    path: str = Field(
        description="Offloaded artifact path, relative to the session scratch dir.",
    )
    start: int = Field(default=0, ge=0, description="Inclusive char offset.")
    end: int | None = Field(default=None, description="Exclusive char offset; null = EOF.")


class ReadOffloadedTool(BaseTool):
    """Read (a slice of) an offloaded tool output."""

    name = "read_offloaded"
    description = "Read a previously offloaded tool result from session scratch."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=10.0)
    input_model = ReadOffloadedInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = ReadOffloadedInput.model_validate(input)
        scratch = ctx.scratch_dir
        if scratch is None:
            return _err(
                "no scratch directory configured for this session",
                root_cause="ctx.scratch_dir is None — nothing to read from",
                safe_retry="start a session with offloading enabled",
                stop_condition="do not retry until scratch is wired",
            )

        rel = Path(args.path)
        if ".." in rel.parts or rel.is_absolute():
            return _err(
                f"path traversal rejected: {args.path}",
                root_cause="path traversal ('..' or absolute) outside scratch dir",
                safe_retry="pass a scratch-relative path with no '..' segments",
                stop_condition="do not retry with the same path",
            )

        target = scratch / rel
        if not target.exists():
            return _err(
                f"offloaded file not found: {args.path}",
                root_cause=f"path not found under scratch: {args.path}",
                safe_retry="verify the offload pointer returned by an earlier tool call",
                stop_condition="do not retry with the same missing path",
            )

        try:
            text = read_offloaded(target, start=args.start, end=args.end)
        except ValueError as exc:
            return _err(
                str(exc),
                root_cause=str(exc),
                safe_retry="pass a scratch-relative path with no '..' segments",
                stop_condition="do not retry with the same path",
            )
        return ToolResult(
            content=text,
            metadata={
                "bytes_read": len(text.encode("utf-8")),
                "start": args.start,
                "end": args.end,
                "summary": f"read {len(text)} chars from {args.path}",
            },
        )


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


__all__ = ["ReadOffloadedInput", "ReadOffloadedTool"]
