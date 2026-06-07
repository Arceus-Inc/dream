"""Default ``edit_file`` tool — string-based file editing.

Spec 05 slice B. Shape borrowed from OpenHarness ``file_edit_tool.py``;
writes go through ``atomic_write_text``. ``replace_all=False`` rewrites only
the first occurrence (matching OpenHarness semantics) but reports
``occurrences`` in metadata so the engine can refuse ambiguous edits.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within
from dream.utils.fs import atomic_write_text


class FileEditInput(BaseModel):
    """Arguments for the ``edit_file`` tool."""

    path: str = Field(description="File path, relative to or within the working directory.")
    old_str: str = Field(description="Existing substring to replace.")
    new_str: str = Field(description="Replacement substring.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


class FileEditTool(BaseTool):
    """Replace text in an existing file."""

    name = "edit_file"
    description = "Edit an existing text file by replacing a substring."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = FileEditInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = FileEditInput.model_validate(input)
        try:
            path = resolve_within(ctx.working_dir, args.path)
        except PathEscapesRoot as exc:
            return _err(
                f"Path outside the working directory: {args.path}",
                root_cause=str(exc),
                safe_retry="pass a path that stays within the working directory",
                stop_condition="do not retry with the same out-of-tree path",
            )

        if not path.exists():
            return _err(
                f"File not found: {path}",
                root_cause=f"path not found: {path}",
                safe_retry="verify the path is correct, or write a new file via write_file",
                stop_condition="do not retry on the same missing path",
            )
        if path.is_dir():
            return _err(
                f"Cannot edit directory: {path}",
                root_cause="path is a directory, not a file",
                safe_retry="pass a file path",
                stop_condition="do not retry on the same directory path",
            )

        if args.old_str == "":
            # ``"x".count("")`` is ``len(x) + 1`` and ``str.replace`` inserts at
            # every boundary — an empty match would silently rewrite the whole
            # file, so reject it before any counting/replacement.
            return _err(
                "old_str must not be empty",
                root_cause="empty old_str matches every position and would corrupt the file",
                safe_retry="pass the exact non-empty substring to replace",
                stop_condition="do not retry with an empty old_str",
            )

        if args.old_str == args.new_str:
            return _err(
                "old_str equals new_str: nothing to do",
                root_cause="noop edit -- old_str is identical to new_str",
                safe_retry="provide a different new_str, or skip the edit",
                stop_condition="do not retry with the same identical arguments",
            )

        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # Strict UTF-8 keeps us from silently corrupting a non-text file on
            # write-back; surface it as a structured error instead.
            return _err(
                f"Cannot edit non-UTF-8 file: {path}",
                root_cause=f"file is not valid UTF-8: {exc}",
                safe_retry="edit a UTF-8 text file, or use a binary-aware tool",
                stop_condition="do not retry editing this file as text",
            )
        occurrences = original.count(args.old_str)
        if occurrences == 0:
            return _err(
                "old_str was not found in the file",
                root_cause="old_str does not match any substring in the file",
                safe_retry="re-read the file with read_file and use the exact substring",
                stop_condition="do not retry with the same old_str",
            )

        if args.replace_all:
            updated = original.replace(args.old_str, args.new_str)
            replacements = occurrences
        else:
            updated = original.replace(args.old_str, args.new_str, 1)
            replacements = 1

        atomic_write_text(path, updated)
        lines_before = original.count("\n") + (0 if original.endswith("\n") else 1)
        lines_after = updated.count("\n") + (0 if updated.endswith("\n") else 1)
        lines_changed = abs(lines_after - lines_before) or replacements
        return ToolResult(
            content=f"Updated {path}",
            metadata={
                "replacements": replacements,
                "occurrences": occurrences,
                "lines_changed": lines_changed,
                "artifacts": [str(path)],
                "summary": f"replaced {replacements} of {occurrences} occurrence(s)",
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


__all__ = ["FileEditInput", "FileEditTool"]
