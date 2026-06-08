"""Repo-write boundary validator (Spec 13A).

A write is in-bounds iff the resolved target path is under the worktree ``cwd``
or under one of the operator-declared ``extra_allowed`` roots. ``resolve()``
collapses symlinks before the check, so a symlink inside the worktree that
points outside it is denied (symlink-escape). Grounded in OpenHarness's
``validate_sandbox_path`` resolve-then-``relative_to(cwd)`` shape.
"""

from __future__ import annotations

from pathlib import Path


def _resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def validate_repo_write(
    path: Path, cwd: Path, extra_allowed: tuple[Path, ...] = ()
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for writing ``path`` under the worktree boundary.

    ``reason`` is empty on success and names the path + boundary on failure.
    Relative paths are anchored at ``cwd``; non-existent targets are permitted
    as long as their (symlink-)resolved location is in-bounds.
    """
    target = path.expanduser()
    if not target.is_absolute():
        target = cwd / target
    resolved = _resolve(target)
    roots = (_resolve(cwd), *(_resolve(root) for root in extra_allowed))
    if any(resolved == root or resolved.is_relative_to(root) for root in roots):
        return True, ""
    return False, f"write outside worktree boundary: {resolved} not under {_resolve(cwd)}"
