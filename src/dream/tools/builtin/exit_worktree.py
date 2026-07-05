"""Default ``exit_worktree`` tool — remove a git worktree (mutating, tier 1).

Ported from OpenHarness ``exit_worktree_tool.py``. Routes through
:func:`dream.utils.git.run_git` (Spec 01 invariant) and confines the target
under the repo root. Companion to ``enter_worktree``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within
from dream.tools.builtin._errors import tool_error as _err
from dream.utils.git import run_git


class ExitWorktreeInput(BaseModel):
    """Arguments for ``exit_worktree``."""

    path: str = Field(description="Worktree path to remove, within the repository.")
    force: bool = Field(default=True, description="Force removal even with local changes.")


class ExitWorktreeTool(BaseTool):
    """Remove a git worktree by path."""

    name = "exit_worktree"
    description = "Remove a git worktree by path."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=30.0)
    input_model = ExitWorktreeInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = ExitWorktreeInput.model_validate(input)
        return ToolEffects(command=f"git worktree remove {args.path}")

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = ExitWorktreeInput.model_validate(input)

        rc, top_level, _ = run_git(["rev-parse", "--show-toplevel"], cwd=Path(ctx.working_dir))
        if rc != 0 or not top_level:
            return _err(
                "exit_worktree requires a git repository.",
                root_cause="`git rev-parse --show-toplevel` failed",
                safe_retry="run inside a git repository",
                stop_condition="do not retry outside a git repo",
            )
        repo_root = Path(top_level)

        try:
            worktree_path = resolve_within(repo_root, args.path)
        except PathEscapesRoot as exc:
            return _err(
                f"Worktree path outside the repository: {args.path}",
                root_cause=str(exc),
                safe_retry="pass a worktree path within the repository",
                stop_condition="do not retry with the same out-of-tree path",
            )

        cmd = ["worktree", "remove"]
        if args.force:
            cmd.append("--force")
        cmd.append(str(worktree_path))
        rc, out, err = run_git(cmd, cwd=repo_root)
        if rc != 0:
            return _err(
                f"git worktree remove failed: {err or out}",
                root_cause=err or out or "non-zero git exit",
                safe_retry="verify the path is a registered worktree",
                stop_condition="do not retry with the same invalid path",
            )
        rel = _display(worktree_path, repo_root)
        return ToolResult(
            content=f"Removed worktree at {rel}.",
            metadata={"path": str(worktree_path), "summary": f"removed worktree {rel}"},
        )


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


__all__ = ["ExitWorktreeInput", "ExitWorktreeTool"]
