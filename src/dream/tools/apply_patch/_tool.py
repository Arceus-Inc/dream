"""Default ``apply_patch`` tool — Codex multi-hunk / multi-file edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools.apply_patch._engine import process_patch
from dream.tools.apply_patch._scan import scan_patch_paths
from dream.tools.apply_patch._summary import summarize_commit
from dream.tools.apply_patch._types import DiffError
from dream.tools.apply_patch._workspace import ConfinedPatchWorkspace
from dream.tools.builtin._errors import tool_error as _err


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
        paths = scan_patch_paths(args.patch)
        return ToolEffects(
            target_paths=tuple(Path(rel) for rel in paths.permission_targets)
        )

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = ApplyPatchInput.model_validate(input)
        body = args.patch.strip()
        if not body:
            return _err(
                "patch must not be empty",
                root_cause="empty patch body",
                safe_retry="pass a *** Begin Patch … *** End Patch body",
                stop_condition="do not retry with an empty patch",
            )

        workspace = ConfinedPatchWorkspace(working_dir=ctx.working_dir)
        if error := workspace.preflight(scan_patch_paths(body)):
            return error

        try:
            result = process_patch(body, workspace.file_ops())
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

        summary = summarize_commit(result.commit)
        return ToolResult(
            content=f"Patch applied: {summary}",
            metadata={
                "status": "success",
                "summary": summary,
                "fuzz": result.fuzz,
                "files": list(result.commit.changes.keys()),
                "change_count": len(result.commit.changes),
            },
        )


__all__ = ["ApplyPatchInput", "ApplyPatchTool"]
