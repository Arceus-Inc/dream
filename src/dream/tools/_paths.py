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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dream.contracts.tool import ToolResult


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


def confine_path(root: Path, candidate: str) -> Path | ToolResult:
    """Resolve ``candidate`` under ``root``, or return the standard escape error.

    Wraps :func:`resolve_within`'s ``PathEscapesRoot`` in the Spec 05 three-part
    ``ToolResult`` every filesystem tool returned by hand. Callers branch on the
    return type: a ``Path`` is the confined target; a ``ToolResult`` is the
    ready-to-return out-of-tree error.
    """
    # Imported lazily so this module stays dependency-light at import time and
    # ``_errors`` (which imports the public contract) can't cycle back here.
    from dream.tools.builtin._errors import tool_error

    try:
        return resolve_within(root, candidate)
    except PathEscapesRoot as exc:
        return tool_error(
            f"Path outside the working directory: {candidate}",
            root_cause=str(exc),
            safe_retry="pass a path that stays within the working directory",
            stop_condition="do not retry with the same out-of-tree path",
        )


__all__ = ["PathEscapesRoot", "confine_path", "resolve_within"]
