"""Shared path-containment helper for filesystem tools.

Filesystem tools accept model-supplied paths, which are untrusted input. A
bare ``Path.resolve`` collapses ``..`` and follows symlinks but does *not*
keep the result inside any boundary: an absolute path (``/etc/passwd``) or a
symlink under scratch that points outside both resolve cleanly to a location
the model should never reach.

``resolve_within`` is the single choke point: it resolves the candidate
against ``root`` (following symlinks, collapsing ``..``) and then verifies the
final location is still under ``root``, raising :class:`PathEscapesRoot`
otherwise. ``root`` itself is resolved first so the comparison is symlink-stable
on both sides.
"""

from __future__ import annotations

from pathlib import Path


class PathEscapesRoot(ValueError):
    """Raised when a resolved candidate path escapes its containment root."""


def resolve_within(root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` under ``root``; reject anything that escapes.

    ``candidate`` may be relative (joined onto ``root``) or absolute; either
    way the fully resolved result must remain under the resolved ``root`` or a
    :class:`PathEscapesRoot` is raised before any filesystem read/write.
    """
    resolved_root = Path(root).expanduser().resolve()
    target = Path(candidate).expanduser()
    if not target.is_absolute():
        target = resolved_root / target
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_root):
        raise PathEscapesRoot(
            f"path escapes the allowed root: {candidate!r} resolves outside {resolved_root}"
        )
    return resolved_target


__all__ = ["PathEscapesRoot", "resolve_within"]
