"""Default ``enter_worktree`` tool — create a git worktree (mutating, tier 1).

Ported from OpenHarness ``enter_worktree_tool.py`` and adapted to dream: all git
runs through the single auditable :func:`dream.utils.git.run_git` wrapper (Spec 01
invariant — no direct ``subprocess``), and an explicit path is confined under the
repo root. Gives the agent an isolated branch checkout for experiments; pair with
``exit_worktree`` to tear it down.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within
from dream.tools.builtin._errors import tool_error as _err
from dream.utils.git import run_git


class EnterWorktreeInput(BaseModel):
    """Arguments for ``enter_worktree``."""

    branch: str = Field(description="Branch name for the worktree.")
    path: str | None = Field(
        default=None,
        description="Worktree path within the repo. Defaults to .harness/worktrees/<branch>.",
    )
    create_branch: bool = Field(default=True, description="Create the branch (vs. check out existing).")
    base_ref: str = Field(default="HEAD", description="Base ref when creating a new branch.")


class EnterWorktreeTool(BaseTool):
    """Create a git worktree and return its path."""

    name = "enter_worktree"
    description = "Create a git worktree for an isolated branch checkout and return its path."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=30.0)
    input_model = EnterWorktreeInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = EnterWorktreeInput.model_validate(input)
        return ToolEffects(command=f"git worktree add {args.branch}")

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = EnterWorktreeInput.model_validate(input)

        rc, top_level, _ = run_git(["rev-parse", "--show-toplevel"], cwd=Path(ctx.working_dir))
        if rc != 0 or not top_level:
            return _err(
                "enter_worktree requires a git repository.",
                root_cause="`git rev-parse --show-toplevel` failed",
                safe_retry="run inside a git repository",
                stop_condition="do not retry outside a git repo",
            )
        repo_root = Path(top_level)

        try:
            worktree_path = _resolve_worktree_path(repo_root, args.branch, args.path)
        except PathEscapesRoot as exc:
            return _err(
                f"Worktree path outside the repository: {args.path}",
                root_cause=str(exc),
                safe_retry="pass a path within the repository, or omit it",
                stop_condition="do not retry with the same out-of-tree path",
            )
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        if args.create_branch:
            cmd = ["worktree", "add", "-b", args.branch, str(worktree_path), args.base_ref]
        else:
            cmd = ["worktree", "add", str(worktree_path), args.branch]
        rc, out, err = run_git(cmd, cwd=repo_root)
        if rc != 0:
            return _err(
                f"git worktree add failed: {err or out}",
                root_cause=err or out or "non-zero git exit",
                safe_retry="check the branch name / base ref and that the path is free",
                stop_condition="do not retry with the same conflicting branch or path",
            )
        rel = _display(worktree_path, repo_root)
        return ToolResult(
            content=f"Created worktree at {rel} on branch {args.branch!r}.",
            metadata={
                "path": str(worktree_path),
                "branch": args.branch,
                "summary": f"worktree {rel}",
            },
        )


def _resolve_worktree_path(repo_root: Path, branch: str, path: str | None) -> Path:
    if path:
        return resolve_within(repo_root, path)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "worktree"
    return (repo_root / ".harness" / "worktrees" / slug).resolve()


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


__all__ = ["EnterWorktreeInput", "EnterWorktreeTool"]
