"""git worktree helpers for branch-isolated tasks (spec 01).

This module starts with the *security boundary*: a slug becomes a directory name
and a branch name, so it is validated before any filesystem or git operation.
The worktree lifecycle (create/resume/remove/cleanup) builds on these in a later
change.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.utils.fs import atomic_write_text
from dream.utils.git import run_git

__all__ = [
    "WorktreeInfo",
    "WorktreeManager",
    "WorktreeSlug",
    "flatten_slug",
    "validate_worktree_slug",
]

_VALID_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_SLUG_LENGTH = 64
_COMMON_SYMLINK_DIRS = ("node_modules", ".venv", "__pycache__", ".tox")


def validate_worktree_slug(slug: str) -> str:
    """Validate a worktree slug; return it unchanged or raise ``ValueError``.

    A security boundary for *both* filesystem paths and git branch names (the
    slug becomes ``worktree-{flat-slug}``), so it enforces path-traversal *and*
    ``git check-ref-format`` constraints:

    - non-empty, at most 64 characters;
    - not an absolute path (no leading ``/`` or ``\\``);
    - each ``/``-separated segment matches ``[a-zA-Z0-9._-]+``;
    - no ``.`` or ``..`` segments (path traversal);
    - per git ref rules: no segment may start/end with ``.``, contain ``..``,
      or end with ``.lock``.
    """
    if not slug:
        raise ValueError("worktree slug must not be empty")

    if len(slug) > _MAX_SLUG_LENGTH:
        raise ValueError(
            f"worktree slug must be {_MAX_SLUG_LENGTH} characters or fewer (got {len(slug)})"
        )

    if slug.startswith(("/", "\\")):
        raise ValueError(f"worktree slug must not be an absolute path: {slug!r}")

    for segment in slug.split("/"):
        if not _VALID_SEGMENT.match(segment):
            raise ValueError(
                f"worktree slug {slug!r}: each segment must be non-empty and contain only "
                "letters, digits, dots, underscores, and dashes"
            )
        if (
            segment.startswith(".")
            or segment.endswith(".")
            or segment.endswith(".lock")
            or ".." in segment
        ):
            raise ValueError(
                f"worktree slug {slug!r}: segment {segment!r} is not a valid git ref component "
                '(no leading/trailing ".", no "..", no ".lock" suffix)'
            )

    return slug


def flatten_slug(slug: str) -> str:
    """Validate then flatten a slug for a flat layout: ``a/b`` -> ``a+b``.

    Validation runs here too, so a caller cannot bypass the security boundary by
    flattening an unvalidated slug straight into a directory name.
    """
    validate_worktree_slug(slug)
    return slug.replace("/", "+")


@dataclass(frozen=True)
class WorktreeSlug:
    """A validated slug. Constructing one *is* the security check.

    Downstream worktree operations take this type rather than a raw ``str``, so
    an unvalidated slug cannot reach a filesystem or git operation.
    """

    value: str

    def __post_init__(self) -> None:
        validate_worktree_slug(self.value)

    @property
    def flat(self) -> str:
        """Flat directory form: ``a/b`` -> ``a+b``."""
        return self.value.replace("/", "+")

    @property
    def branch(self) -> str:
        """The generated git branch name for this worktree."""
        return f"worktree-{self.flat}"


@dataclass(frozen=True)
class WorktreeInfo:
    """Metadata describing a managed git worktree."""

    slug: str
    path: Path
    branch: str
    original_path: Path
    created_at: float
    agent_id: str | None = None


def _symlink_common_dirs(repo: Path, worktree: Path) -> None:
    """Symlink large shared dirs into the worktree; failures are non-fatal."""
    for name in _COMMON_SYMLINK_DIRS:
        src = repo / name
        dst = worktree / name
        if dst.exists() or dst.is_symlink() or not src.exists():
            continue
        # disk full / unsupported fs — non-fatal, the worktree still works
        with contextlib.suppress(OSError):
            dst.symlink_to(src)


def _remove_symlinks(worktree: Path) -> None:
    """Unlink common-dir symlinks before git removes the worktree directory."""
    for name in _COMMON_SYMLINK_DIRS:
        dst = worktree / name
        if dst.is_symlink():
            with contextlib.suppress(OSError):
                dst.unlink()


class WorktreeManager:
    """Create, resume, list, remove, and prune per-task git worktrees.

    Worktrees live under ``<repo>/.dream/worktrees/<flat-slug>/`` (git-ignored).
    A sibling ``<flat-slug>.meta.json`` persists the owning ``agent_id`` and
    creation time so ``cleanup_stale`` can prune orphans after a crash.
    """

    def __init__(self, paths: DreamPaths) -> None:
        self._paths = paths

    @property
    def base_dir(self) -> Path:
        return self._paths.worktrees_dir

    def create_worktree(
        self,
        slug: str | WorktreeSlug,
        *,
        agent_id: str | None = None,
        start_point: str = "HEAD",
    ) -> WorktreeInfo:
        """Create (or fast-resume) the worktree for ``slug`` from ``start_point``.

        ``start_point`` is the commit/ref to check out (default ``HEAD``); resume
        passes a checkpoint ref here.
        """
        wt = slug if isinstance(slug, WorktreeSlug) else WorktreeSlug(slug)
        repo = self._paths.repo
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / wt.flat

        # Fast resume: an existing valid worktree is returned as-is.
        if path.exists() and run_git(["rev-parse", "--git-dir"], cwd=path)[0] == 0:
            agent, created = self._read_meta(wt.flat)
            return WorktreeInfo(
                slug=wt.value,
                path=path,
                branch=wt.branch,
                original_path=repo,
                created_at=created if created is not None else path.stat().st_mtime,
                agent_id=agent,
            )

        # -B resets an orphan branch left by a prior remove rather than colliding.
        code, _, stderr = run_git(
            ["worktree", "add", "-B", wt.branch, str(path), start_point], cwd=repo
        )
        if code != 0:
            raise RuntimeError(f"git worktree add failed: {stderr}")

        _symlink_common_dirs(repo, path)
        created_at = time.time()
        self._write_meta(wt.flat, agent_id=agent_id, created_at=created_at)
        return WorktreeInfo(
            slug=wt.value,
            path=path,
            branch=wt.branch,
            original_path=repo,
            created_at=created_at,
            agent_id=agent_id,
        )

    def remove_worktree(self, slug: str | WorktreeSlug) -> bool:
        """Remove a worktree (symlinks first). Returns False if it was absent."""
        wt = slug if isinstance(slug, WorktreeSlug) else WorktreeSlug(slug)
        path = self.base_dir / wt.flat
        if not path.exists():
            return False
        _remove_symlinks(path)
        run_git(["worktree", "remove", "--force", str(path)], cwd=self._paths.repo)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self._meta_path(wt.flat).unlink(missing_ok=True)
        return not path.exists()

    def list_worktrees(self) -> list[WorktreeInfo]:
        """Return info for every valid worktree under ``base_dir``."""
        if not self.base_dir.exists():
            return []
        out: list[WorktreeInfo] = []
        for child in sorted(self.base_dir.iterdir()):
            if not child.is_dir():
                continue
            if run_git(["rev-parse", "--git-dir"], cwd=child)[0] != 0:
                continue
            rc, branch_out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=child)
            branch = branch_out if rc == 0 else "unknown"
            rc2, common, _ = run_git(["rev-parse", "--git-common-dir"], cwd=child)
            original = Path(common).resolve().parent if rc2 == 0 and common else child
            agent, created = self._read_meta(child.name)
            out.append(
                WorktreeInfo(
                    slug=child.name.replace("+", "/"),
                    path=child,
                    branch=branch,
                    original_path=original,
                    created_at=created if created is not None else child.stat().st_mtime,
                    agent_id=agent,
                )
            )
        return out

    def cleanup_stale(self, active_agent_ids: set[str] | None = None) -> list[str]:
        """Remove agent-owned worktrees whose agent is not in ``active_agent_ids``.

        ``None`` treats *every* agent-owned worktree as stale (full sweep).
        """
        removed: list[str] = []
        for info in self.list_worktrees():
            if info.agent_id is None:
                continue
            if active_agent_ids is not None and info.agent_id in active_agent_ids:
                continue
            if self.remove_worktree(WorktreeSlug(info.slug)):
                removed.append(info.slug)
        return removed

    # --- agent-id / created-at persistence (sibling meta file) ---

    def _meta_path(self, flat: str) -> Path:
        return self.base_dir / f"{flat}.meta.json"

    def _write_meta(self, flat: str, *, agent_id: str | None, created_at: float) -> None:
        atomic_write_text(
            self._meta_path(flat),
            json.dumps({"agent_id": agent_id, "created_at": created_at}),
        )

    def _read_meta(self, flat: str) -> tuple[str | None, float | None]:
        """Return ``(agent_id, created_at)`` from the sibling meta file, if any."""
        path = self._meta_path(flat)
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None, None
        if not isinstance(data, dict):
            return None, None
        agent = data.get("agent_id")
        created = data.get("created_at")
        return (
            agent if isinstance(agent, str) else None,
            float(created) if isinstance(created, (int, float)) else None,
        )
