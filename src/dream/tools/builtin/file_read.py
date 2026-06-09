"""Default ``read_file`` tool — UTF-8 text file with line-numbered slicing.

Spec 05 slice B. Shape borrowed from OpenHarness ``file_read_tool.py``;
errors emit the spec 05 three-part contract (root_cause / safe_retry /
stop_condition) in metadata so ``derive_observation`` lifts them into
``Observation.next_actions``.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import confine_path
from dream.tools.builtin._errors import tool_error as _err

# Bytes sampled to decide text-vs-binary; large enough to catch a leading NUL
# in any real binary, small enough not to load a big file up front.
_BINARY_SNIFF_BYTES = 8192


class FileReadInput(BaseModel):
    """Arguments for the ``read_file`` tool."""

    path: str = Field(description="File path, relative to or within the working directory.")
    offset: int = Field(default=0, ge=0, description="Zero-based starting line.")
    limit: int = Field(default=2000, ge=1, le=10000, description="Max lines to return.")


class FileReadTool(BaseTool):
    """Read a UTF-8 text file with line numbers."""

    name = "read_file"
    description = "Read a text file from the local repository."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=10.0)
    input_model = FileReadInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        # A read still reports its path so the credential guard can block
        # reading a secret (the guard is effect-agnostic).
        args = FileReadInput.model_validate(input)
        return ToolEffects(target_paths=(Path(args.path),))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = FileReadInput.model_validate(input)
        path = confine_path(ctx.working_dir, args.path)
        if isinstance(path, ToolResult):
            return path

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

        # Binary sniff on a bounded prefix only — reading the whole file just to
        # detect a NUL byte would defeat the windowed read below.
        with path.open("rb") as fb:
            prefix = fb.read(_BINARY_SNIFF_BYTES)
        if b"\x00" in prefix:
            return _err(
                f"Binary file: {path}",
                root_cause="binary content (NUL byte) cannot be read as text",
                safe_retry="use a binary-aware tool, or read a different file",
                stop_condition="do not retry on the same binary path",
            )

        # Stream only the requested line window instead of loading the whole
        # file: ``islice`` advances the file iterator without materialising the
        # skipped or trailing lines, so a small slice of a huge file stays cheap.
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            window = itertools.islice(fh, args.offset, args.offset + args.limit)
            selected = [line.rstrip("\r\n") for line in window]
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


__all__ = ["FileReadInput", "FileReadTool"]
