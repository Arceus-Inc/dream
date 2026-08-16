"""Short-lived git worktrees for ``IsolationMode.WORKTREE`` subagents.

Adapted from OpenHarness ``WorktreeManager`` / Dream ``enter_worktree``: one
create + remove pair per child session, paths confined under the session scratch
dir so they never land in the parent's durable worktree.

Edits in the child worktree are ephemeral. ``remove`` force-deletes the
checkout and its branch; nothing is merged back to the parent.
"""

from __future__ import annotations

import contextlib
import re
import shutil
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
        """Best-effort teardown: drop the worktree, then the ephemeral branch."""
        forget_worktree(self.repo_root, self.path, branch=self.branch)


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
            forget_worktree(repo_root, path, branch=branch)
            raise RuntimeError(f"git worktree add failed: {err or out}")
        return SubagentWorktree(path=path, branch=branch, repo_root=repo_root)


def forget_worktree(repo_root: Path, path: Path, *, branch: str | None = None) -> None:
    """Drop git worktree metadata, then the filesystem path.

    Order is fail-closed: unregister (``worktree remove``), prune stale
    admin files, delete the ephemeral branch, then ``rmtree``. Used both
    for normal teardown and a failed ``worktree add`` that may have
    registered metadata before returning an error.
    """
    with contextlib.suppress(Exception):
        run_git(["worktree", "remove", "--force", str(path)], cwd=repo_root)
    with contextlib.suppress(Exception):
        run_git(["worktree", "prune"], cwd=repo_root)
    if branch:
        with contextlib.suppress(Exception):
            run_git(["branch", "-D", branch], cwd=repo_root)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _safe_slug(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-")
    return (slug or "agent")[:48]


__all__ = ["SubagentWorktree", "SubagentWorktreeFactory", "forget_worktree"]
