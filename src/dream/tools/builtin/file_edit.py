"""Default ``edit_file`` tool — string-based file editing.

Spec 05 slice B. Shape borrowed from OpenHarness ``file_edit_tool.py``;
writes go through ``atomic_write_text``. ``replace_all=False`` rewrites only
the first occurrence (matching OpenHarness semantics) but reports
``occurrences`` in metadata so the engine can refuse ambiguous edits.
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

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = FileEditInput.model_validate(input)
        return ToolEffects(target_paths=(Path(args.path),))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = FileEditInput.model_validate(input)
        path = confine_path(ctx.working_dir, args.path)
        if isinstance(path, ToolResult):
            return path

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

        # Mutation honesty (Hermes-simple): refuse ambiguous single-replace so the
        # agent cannot claim a unique edit when several matches exist.
        if occurrences > 1 and not args.replace_all:
            return ToolResult(
                content=f"old_str matches {occurrences} places; refuse ambiguous edit",
                is_error=True,
                metadata={
                    "root_cause": "non-unique old_str without replace_all",
                    "safe_retry": (
                        "pass more surrounding context to make old_str unique, "
                        "or set replace_all=true"
                    ),
                    "stop_condition": "do not retry the same ambiguous old_str",
                    "occurrences": occurrences,
                    "status": "error",
                    "summary": "ambiguous edit refused",
                    "next_actions": (
                        "narrow old_str with surrounding context",
                        "or set replace_all=true",
                    ),
                },
            )

        if args.replace_all:
            updated = original.replace(args.old_str, args.new_str)
            replacements = occurrences
        else:
            updated = original.replace(args.old_str, args.new_str, 1)
            replacements = 1

        atomic_write_text(path, updated)
        # Verify disk matches claim (write honesty).
        try:
            on_disk = path.read_text(encoding="utf-8")
        except OSError as exc:
            return _err(
                f"edit claimed success but could not re-read {path}: {exc}",
                root_cause=str(exc),
                safe_retry="re-read the file and retry the edit",
                stop_condition="stop if re-read keeps failing",
            )
        if on_disk != updated:
            return _err(
                f"edit did not persist to disk: {path}",
                root_cause="post-write content mismatch",
                safe_retry="retry the edit after confirming filesystem health",
                stop_condition="do not claim success when disk content differs",
            )
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
                "status": "success",
                "next_actions": ("continue implementing or run tests",),
            },
        )


__all__ = ["FileEditInput", "FileEditTool"]
