"""Default ``apply_patch`` tool — Codex multi-hunk / multi-file edits.

Replaces the former ``edit_file`` substring tool: one patch can add, update,
move, or delete files. Tiny single-line fixes still use the same format
(``*** Update File`` + one hunk). Writes go through ``atomic_write_text``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._apply_patch import (
    ActionType,
    DiffError,
    identify_files_added,
    identify_files_needed,
    process_patch,
)
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import confine_path
from dream.tools.builtin._errors import tool_error as _err
from dream.utils.fs import atomic_write_text


class ApplyPatchInput(BaseModel):
    """Arguments for the ``apply_patch`` tool."""

    patch: str = Field(
        description=(
            "Codex apply_patch body: *** Begin Patch … *** End Patch with "
            "*** Add File: / *** Update File: / *** Delete File: hunks."
        )
    )


class ApplyPatchTool(BaseTool):
    """Apply a multi-hunk, multi-file patch under the working directory."""

    name = "apply_patch"
    description = (
        "Apply a structured multi-hunk patch (add/update/delete/move files). "
        "Prefer this for all code edits — one call can touch many sites."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=30.0)
    input_model = ApplyPatchInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = ApplyPatchInput.model_validate(input)
        paths = (
            *identify_files_needed(args.patch),
            *identify_files_added(args.patch),
        )
        # Move targets appear as "*** Move to: " — include for permission gate.
        move_paths = [
            line[len("*** Move to: ") :]
            for line in args.patch.splitlines()
            if line.startswith("*** Move to: ")
        ]
        targets = tuple(Path(p) for p in (*paths, *move_paths) if p)
        return ToolEffects(target_paths=targets)

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = ApplyPatchInput.model_validate(input)
        patch = args.patch.strip()
        if not patch:
            return _err(
                "patch must not be empty",
                root_cause="empty patch body",
                safe_retry="pass a *** Begin Patch … *** End Patch body",
                stop_condition="do not retry with an empty patch",
            )

        confined: dict[str, Path] = {}

        def _resolve(rel: str) -> Path | ToolResult:
            if rel in confined:
                return confined[rel]
            path = confine_path(ctx.working_dir, rel)
            if isinstance(path, ToolResult):
                return path
            confined[rel] = path
            return path

        # Pre-resolve every path named in the patch so we fail before any write.
        for rel in (
            *identify_files_needed(patch),
            *identify_files_added(patch),
            *(
                line[len("*** Move to: ") :]
                for line in patch.splitlines()
                if line.startswith("*** Move to: ")
            ),
        ):
            resolved = _resolve(rel)
            if isinstance(resolved, ToolResult):
                return resolved

        def open_fn(rel: str) -> str:
            path = confined[rel]
            return path.read_text(encoding="utf-8")

        def write_fn(rel: str, content: str) -> None:
            path = confined.get(rel)
            if path is None:
                resolved = _resolve(rel)
                if isinstance(resolved, ToolResult):
                    raise DiffError(f"path escapes working dir: {rel}")
                path = resolved
            if path.exists() and path.is_dir():
                raise DiffError(f"Cannot write to directory: {rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, content)

        def remove_fn(rel: str) -> None:
            path = confined[rel]
            if path.exists() and path.is_file():
                path.unlink()

        try:
            _message, fuzz, commit = process_patch(patch, open_fn, write_fn, remove_fn)
        except UnicodeDecodeError as exc:
            return _err(
                f"Cannot patch non-UTF-8 file: {exc}",
                root_cause=f"file is not valid UTF-8: {exc}",
                safe_retry="patch UTF-8 text files only",
                stop_condition="do not retry editing this file as text",
            )
        except DiffError as exc:
            return _err(
                str(exc),
                root_cause=str(exc),
                safe_retry="re-read the file and emit a patch with exact context lines",
                stop_condition="do not retry the same failing patch unchanged",
            )
        except OSError as exc:
            return _err(
                f"Filesystem error applying patch: {exc}",
                root_cause=str(exc),
                safe_retry="check path permissions, then retry",
                stop_condition="stop if the filesystem error persists",
            )

        summary_parts: list[str] = []
        for path, change in commit.changes.items():
            if change.type == ActionType.ADD:
                summary_parts.append(f"added {path}")
            elif change.type == ActionType.DELETE:
                summary_parts.append(f"deleted {path}")
            elif change.move_path:
                summary_parts.append(f"moved {path} -> {change.move_path}")
            else:
                summary_parts.append(f"updated {path}")

        summary = "; ".join(summary_parts) if summary_parts else "no changes"
        return ToolResult(
            content=f"Patch applied: {summary}",
            metadata={
                "status": "success",
                "summary": summary,
                "fuzz": fuzz,
                "files": list(commit.changes.keys()),
                "change_count": len(commit.changes),
            },
        )


__all__ = ["ApplyPatchInput", "ApplyPatchTool"]
