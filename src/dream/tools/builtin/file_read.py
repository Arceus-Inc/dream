"""Default ``read_file`` tool — UTF-8 text file with line-numbered slicing.

Spec 05 slice B. Shape borrowed from OpenHarness ``file_read_tool.py``;
errors emit the spec 05 three-part contract (root_cause / safe_retry /
stop_condition) in metadata so ``derive_observation`` lifts them into
``Observation.next_actions``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class FileReadInput(BaseModel):
    """Arguments for the ``read_file`` tool."""

    path: str = Field(description="File path, absolute or relative to cwd.")
    offset: int = Field(default=0, ge=0, description="Zero-based starting line.")
    limit: int = Field(default=2000, ge=1, le=10000, description="Max lines to return.")


class FileReadTool(BaseTool):
    """Read a UTF-8 text file with line numbers."""

    name = "read_file"
    description = "Read a text file from the local repository."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=10.0)
    input_model = FileReadInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = FileReadInput.model_validate(input)
        path = _resolve(ctx.working_dir, args.path)

        if not path.exists():
            return _err(
                f"File not found: {path}",
                root_cause=f"path does not exist: {path}",
                safe_retry="verify the path is correct and try again",
                stop_condition="do not retry without a different path",
            )
        if path.is_dir():
            return _err(
                f"Cannot read directory: {path}",
                root_cause="path is a directory, not a file",
                safe_retry="pass a file path or use a directory-listing tool",
                stop_condition="do not retry on the same directory path",
            )

        raw = path.read_bytes()
        if b"\x00" in raw:
            return _err(
                f"Binary file: {path}",
                root_cause="binary content (NUL byte) cannot be read as text",
                safe_retry="use a binary-aware tool, or read a different file",
                stop_condition="do not retry on the same binary path",
            )

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[args.offset : args.offset + args.limit]
        if not selected:
            return ToolResult(
                content=f"(no content in selected range for {path})",
                metadata={
                    "lines_returned": 0,
                    "summary": f"no content in range [{args.offset}, {args.offset + args.limit})",
                },
            )
        body = "\n".join(f"{args.offset + idx + 1:>6}\t{line}" for idx, line in enumerate(selected))
        return ToolResult(
            content=body,
            metadata={
                "lines_returned": len(selected),
                "summary": f"{len(selected)} lines",
            },
        )


def _resolve(base: Path, candidate: str) -> Path:
    p = Path(candidate).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


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


__all__ = ["FileReadInput", "FileReadTool"]
