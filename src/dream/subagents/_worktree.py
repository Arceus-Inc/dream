"""Short-lived git worktrees for ``IsolationMode.WORKTREE`` subagents.

Adapted from OpenHarness ``WorktreeManager`` / Dream ``enter_worktree``: one
create + remove pair per child session, paths confined under the session scratch
dir so they never land in the parent's durable worktree.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from dream.utils.git import run_git


@dataclass(frozen=True)
class SubagentWorktree:
    """One isolated checkout owned by a single subagent run."""

    path: Path
    branch: str
    repo_root: Path

    def remove(self) -> None:
        """Force-remove the worktree and delete its ephemeral branch."""
        run_git(
            ["worktree", "remove", "--force", str(self.path)],
            cwd=self.repo_root,
        )
        run_git(["branch", "-D", self.branch], cwd=self.repo_root)


@dataclass(frozen=True)
class SubagentWorktreeFactory:
    """Mint worktrees under ``scratch_dir / subagent-worktrees``."""

    scratch_dir: Path
    parent_cwd: Path

    def create(self, agent_name: str) -> SubagentWorktree:
        rc, top_level, err = run_git(
            ["rev-parse", "--show-toplevel"],
            cwd=self.parent_cwd,
        )
        if rc != 0 or not top_level:
            raise RuntimeError(
                f"worktree isolation requires a git repository: {err or 'rev-parse failed'}"
            )
        repo_root = Path(top_level)
        slug = _safe_slug(agent_name)
        branch = f"dream-subagent/{slug}-{uuid.uuid4().hex[:8]}"
        path = (self.scratch_dir / "subagent-worktrees" / branch.replace("/", "+")).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        rc, out, err = run_git(
            ["worktree", "add", "-b", branch, str(path), "HEAD"],
            cwd=repo_root,
        )
        if rc != 0:
            raise RuntimeError(f"git worktree add failed: {err or out}")
        return SubagentWorktree(path=path, branch=branch, repo_root=repo_root)


def _safe_slug(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-")
    return (slug or "agent")[:48]


__all__ = ["SubagentWorktree", "SubagentWorktreeFactory"]
