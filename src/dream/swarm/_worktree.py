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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.utils.file_lock import exclusive_file_lock
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
        exist_ok: bool = True,
    ) -> WorktreeInfo:
        """Create (or fast-resume) the worktree for ``slug`` from ``start_point``.

        ``start_point`` is the commit/ref to check out (default ``HEAD``); resume
        passes a checkpoint ref here. With ``exist_ok=False`` an already-present
        valid worktree raises :class:`FileExistsError` instead of fast-resuming —
        callers that require a fresh, never-reused id (``resume_from``) use this to
        make the uniqueness check atomic under the per-slug lock.
        """
        wt = slug if isinstance(slug, WorktreeSlug) else WorktreeSlug(slug)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / wt.flat

        # Serialise the whole check-then-add-then-meta sequence per slug. Without
        # the lock, two concurrent creators both fail the fast-resume check and
        # both run ``git worktree add -B``, force-resetting a branch that may be
        # live in the other's worktree; their ``.meta.json`` writes also race.
        with exclusive_file_lock(self._lock_path(wt.flat)):
            # An existing valid worktree: fast-resume, or refuse if exist_ok=False.
            if path.exists() and run_git(["rev-parse", "--git-dir"], cwd=path)[0] == 0:
                return self._resume_existing(wt, path, exist_ok=exist_ok)
            return self._create_fresh(
                wt, path, start_point=start_point, agent_id=agent_id
            )

    def _resume_existing(
        self, wt: WorktreeSlug, path: Path, *, exist_ok: bool
    ) -> WorktreeInfo:
        """Fast-resume an already-present valid worktree (or refuse it)."""
        if not exist_ok:
            raise FileExistsError(f"worktree for slug {wt.value!r} already exists")
        agent, created = self._read_meta(wt.flat)
        return WorktreeInfo(
            slug=wt.value,
            path=path,
            branch=wt.branch,
            original_path=self._paths.repo,
            created_at=created if created is not None else path.stat().st_mtime,
            agent_id=agent,
        )

    def _create_fresh(
        self,
        wt: WorktreeSlug,
        path: Path,
        *,
        start_point: str,
        agent_id: str | None,
    ) -> WorktreeInfo:
        """Run ``git worktree add`` and persist symlinks + meta for a new tree."""
        repo = self._paths.repo
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
        # Same per-slug lock as ``create_worktree`` so a remove can't interleave
        # with a concurrent create of the same slug (half-created teardown).
        with exclusive_file_lock(self._lock_path(wt.flat)):
            if not path.exists():
                return False
            _remove_symlinks(path)
            code, _, _ = run_git(
                ["worktree", "remove", "--force", str(path)], cwd=self._paths.repo
            )
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            if code != 0:
                # ``git worktree remove`` failed (e.g. the dir was already gone, or
                # locked): its registration under ``.git/worktrees/`` was not cleared.
                # Prune it so ``git worktree list`` doesn't accumulate phantom entries.
                run_git(["worktree", "prune"], cwd=self._paths.repo)
            self._meta_path(wt.flat).unlink(missing_ok=True)
            return not path.exists()

    def list_worktrees(self) -> list[WorktreeInfo]:
        """Return info for every valid worktree under ``base_dir``."""
        return list(self._iter_worktrees())

    def _iter_worktrees(self) -> Iterator[WorktreeInfo]:
        """Yield :class:`WorktreeInfo` for each valid worktree, in name order."""
        if not self.base_dir.exists():
            return
        for child in sorted(self.base_dir.iterdir()):
            info = self._worktree_info_for(child)
            if info is not None:
                yield info

    def _worktree_info_for(self, child: Path) -> WorktreeInfo | None:
        """Describe one ``base_dir`` child, or ``None`` if it isn't a worktree."""
        if not child.is_dir():
            return None
        if run_git(["rev-parse", "--git-dir"], cwd=child)[0] != 0:
            return None
        rc, branch_out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=child)
        branch = branch_out if rc == 0 else "unknown"
        rc2, common, _ = run_git(["rev-parse", "--git-common-dir"], cwd=child)
        original = Path(common).resolve().parent if rc2 == 0 and common else child
        agent, created = self._read_meta(child.name)
        return WorktreeInfo(
            slug=child.name.replace("+", "/"),
            path=child,
            branch=branch,
            original_path=original,
            created_at=created if created is not None else child.stat().st_mtime,
            agent_id=agent,
        )

    def cleanup_stale(self, active_agent_ids: set[str] | None = None) -> list[str]:
        """Remove agent-owned worktrees whose agent is not in ``active_agent_ids``.

        ``None`` treats *every* agent-owned worktree as stale (full sweep).
        """
        removed: list[str] = []
        for info in self._iter_worktrees():
            if info.agent_id is None:
                continue
            if active_agent_ids is not None and info.agent_id in active_agent_ids:
                continue
            if self.remove_worktree(WorktreeSlug(info.slug)):
                removed.append(info.slug)
        return removed

    # --- agent-id / created-at persistence (sibling meta file) ---

    def _lock_path(self, flat: str) -> Path:
        """Per-slug lock file — a sibling of the worktree dir, so acquiring it
        never recreates a worktree that ``remove_worktree`` just deleted."""
        return self.base_dir / f"{flat}.lock"

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
