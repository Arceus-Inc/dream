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
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import confine_path
from dream.tools.builtin._errors import tool_error as _err
from dream.utils.fs import atomic_write_text


class FileWriteInput(BaseModel):
    """Arguments for the ``write_file`` tool."""

    path: str = Field(description="File path, relative to or within the working directory.")
    content: str = Field(description="Full file contents to write.")


class FileWriteTool(BaseTool):
    """Create or overwrite a text file atomically."""

    name = "write_file"
    description = "Create or overwrite a text file in the local repository."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = FileWriteInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = FileWriteInput.model_validate(input)
        return ToolEffects(target_paths=(Path(args.path),))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = FileWriteInput.model_validate(input)
        path = confine_path(ctx.working_dir, args.path)
        if isinstance(path, ToolResult):
            return path

        if path.exists() and path.is_dir():
            return _err(
                f"Cannot write to directory: {path}",
                root_cause="path resolves to an existing directory",
                safe_retry="pass a file path, not a directory",
                stop_condition="do not retry on the same directory path",
            )

        try:
            atomic_write_text(path, args.content)
        except OSError as exc:
            # Permission denied, disk full, invalid path component, etc. —
            # surface a structured tool error instead of crashing the act-loop.
            return _err(
                f"Could not write file: {exc}",
                root_cause=str(exc),
                safe_retry="check directory permissions and available disk space",
                stop_condition="do not retry until the underlying write error is resolved",
            )
        encoded = args.content.encode("utf-8")
        return ToolResult(
            content=f"Wrote {path}",
            metadata={
                "bytes_written": len(encoded),
                "artifacts": [str(path)],
                "summary": f"wrote {len(encoded)} bytes",
            },
        )


__all__ = ["FileWriteInput", "FileWriteTool"]
