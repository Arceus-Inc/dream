"""Default ``write_file`` tool — overwrite a UTF-8 file atomically.

Spec 05 slice B. Routes every write through
``dream.utils.fs.atomic_write_text`` (spec 01 invariant: harness-initiated
writes must be atomic). Shape borrowed from OpenHarness
``file_write_tool.py`` minus the approval-prompt path, which lives in the
engine in dream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.utils.fs import atomic_write_text


class FileWriteInput(BaseModel):
    """Arguments for the ``write_file`` tool."""

    path: str = Field(description="File path, absolute or relative to cwd.")
    content: str = Field(description="Full file contents to write.")


class FileWriteTool(BaseTool):
    """Create or overwrite a text file atomically."""

    name = "write_file"
    description = "Create or overwrite a text file in the local repository."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = FileWriteInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = FileWriteInput.model_validate(input)
        path = _resolve(ctx.working_dir, args.path)

        if path.exists() and path.is_dir():
            return _err(
                f"Cannot write to directory: {path}",
                root_cause="path resolves to an existing directory",
                safe_retry="pass a file path, not a directory",
                stop_condition="do not retry on the same directory path",
            )

        atomic_write_text(path, args.content)
        encoded = args.content.encode("utf-8")
        return ToolResult(
            content=f"Wrote {path}",
            metadata={
                "bytes_written": len(encoded),
                "artifacts": [str(path)],
                "summary": f"wrote {len(encoded)} bytes",
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


__all__ = ["FileWriteInput", "FileWriteTool"]
