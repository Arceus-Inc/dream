"""Checkpoints as git refs, and resume-from-checkpoint (spec 01).

After a successful turn the runner commits the worktree and records the SHA at
``refs/dream/checkpoints/{task}/{n}`` — a ref, not a branch, so it stays out of
``git branch`` *and* **survives worktree teardown**: the disposable worktree is
deleted, the checkpoint ref (and the commit it pins) persists. Resume spawns a
fresh worktree at any checkpoint with parent lineage recorded in its state.json.
"""

from __future__ import annotations

import time

from dream.config.paths import DreamPaths
from dream.state.sidecar import create_sidecar
from dream.swarm._worktree import WorktreeInfo, WorktreeManager
from dream.utils.git import run_git

__all__ = [
    "DONE_CHECKPOINT",
    "gc_checkpoints",
    "list_checkpoints",
    "resume_from",
    "write_checkpoint",
    "write_done",
]

# The ref leaf for the final success checkpoint; never garbage-collected.
DONE_CHECKPOINT = "done"


def _checkpoint_prefix(paths: DreamPaths, task_id: str) -> str:
    """``refs/dream/checkpoints/{task}`` (validates task_id via paths)."""
    return paths.checkpoint_ref(task_id, 0).rsplit("/", 1)[0]


def write_checkpoint(paths: DreamPaths, task_id: str, turn: int) -> str:
    """Commit the worktree and pin ``refs/dream/checkpoints/{task}/{turn}``.

    Returns the checkpoint commit SHA. Uses ``--allow-empty`` so every successful
    turn produces a checkpoint even when no files changed.
    """
    worktree = paths.worktree(task_id)
    run_git(["add", "-A"], cwd=worktree)
    run_git(["commit", "--allow-empty", "-m", f"checkpoint: turn {turn}"], cwd=worktree)
    code, sha, err = run_git(["rev-parse", "HEAD"], cwd=worktree)
    if code != 0:
        raise RuntimeError(f"could not read worktree HEAD: {err}")
    run_git(["update-ref", paths.checkpoint_ref(task_id, turn), sha], cwd=worktree)
    return sha


def write_done(paths: DreamPaths, task_id: str) -> str:
    """Pin the final ``refs/dream/checkpoints/{task}/done`` at the current HEAD."""
    worktree = paths.worktree(task_id)
    code, sha, err = run_git(["rev-parse", "HEAD"], cwd=worktree)
    if code != 0:
        raise RuntimeError(f"could not read worktree HEAD: {err}")
    run_git(["update-ref", paths.checkpoint_ref(task_id, DONE_CHECKPOINT), sha], cwd=worktree)
    return sha


def list_checkpoints(paths: DreamPaths, task_id: str) -> list[tuple[str, str]]:
    """Return sorted ``(name, sha)`` for a task's checkpoints (name = ref leaf)."""
    prefix = _checkpoint_prefix(paths, task_id)
    _, out, _ = run_git(
        ["for-each-ref", "--format=%(refname) %(objectname)", prefix], cwd=paths.repo
    )
    result: list[tuple[str, str]] = []
    for line in out.splitlines():
        refname, sha = line.split(" ", 1)
        result.append((refname[len(prefix) + 1 :], sha))
    return sorted(result)


def resume_from(
    paths: DreamPaths,
    source_ref: str,
    new_task_id: str,
    *,
    harness_version: str,
    base_branch: str | None = None,
) -> WorktreeInfo:
    """Spawn a fresh worktree (new task-id) at ``source_ref`` with parent lineage.

    Task-ids are never reused: refuses if a worktree or sidecar already exists for
    ``new_task_id``.
    """
    if paths.worktree(new_task_id).exists() or paths.sidecar(new_task_id).exists():
        raise ValueError(f"task-id already in use: {new_task_id!r}")
    info = WorktreeManager(paths).create_worktree(new_task_id, start_point=source_ref)
    if base_branch is None:
        code, branch, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=paths.repo)
        base_branch = branch if code == 0 and branch else "main"
    create_sidecar(
        paths,
        new_task_id,
        base_branch=base_branch,
        harness_version=harness_version,
        parent_checkpoint_ref=source_ref,
    )
    return info


def gc_checkpoints(paths: DreamPaths, task_id: str, *, older_than_days: int = 30) -> list[str]:
    """Delete checkpoint refs whose commit is older than the window; keep ``done``."""
    cutoff = time.time() - older_than_days * 86400
    removed: list[str] = []
    for name, sha in list_checkpoints(paths, task_id):
        if name == DONE_CHECKPOINT:
            continue
        code, committed_at, _ = run_git(["show", "-s", "--format=%ct", sha], cwd=paths.repo)
        if code == 0 and committed_at and int(committed_at) < cutoff:
            run_git(["update-ref", "-d", paths.checkpoint_ref(task_id, name)], cwd=paths.repo)
            removed.append(name)
    return removed
