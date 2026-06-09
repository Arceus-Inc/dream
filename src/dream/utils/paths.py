"""Shared path canonicalization for the permissions layer.

One sequence — *expanduser → anchor at cwd → normpath (lexical) → resolve
(strict=False) with an OSError fallback* — was hand-rolled three times in the
permissions package (the checker's deny-glob forms, the credential guard's
candidates, and the repo-write boundary validator). Centralising it here keeps
the symlink-escape semantics identical across all three: the *lexical* form
collapses ``..``/``.`` without following symlinks, while the *resolved* form
follows them, so neither a symlinked parent nor a symlink pointing into a
guarded location can dodge a check.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CanonicalPath", "canonical_path_forms"]


@dataclass(frozen=True)
class CanonicalPath:
    """The canonical forms of a path, anchored at a cwd.

    * ``anchored`` — ``path`` after ``expanduser()`` and, if relative, joined
      onto ``cwd`` (no normalisation, no symlink following).
    * ``lexical`` — ``anchored`` with ``..``/``.`` collapsed (``os.path.normpath``);
      symlinks are *not* followed.
    * ``resolved`` — ``anchored.resolve(strict=False)``, or ``None`` when that
      raised ``OSError`` (e.g. an ELOOP symlink cycle). Symlinks *are* followed.
    """

    anchored: Path
    lexical: Path
    resolved: Path | None


def canonical_path_forms(path: Path, cwd: Path) -> CanonicalPath:
    """Anchor ``path`` at ``cwd`` and return its lexical + resolved forms.

    The single shared implementation of the permissions layer's path
    canonicalisation. ``resolved`` is ``None`` on ``OSError`` so callers choose
    their own fallback (the lexical form, or ``anchored.absolute()``).
    """
    anchored = path.expanduser()
    if not anchored.is_absolute():
        anchored = cwd / anchored
    lexical = Path(os.path.normpath(anchored.as_posix()))
    try:
        resolved: Path | None = anchored.resolve(strict=False)
    except OSError:
        resolved = None
    return CanonicalPath(anchored=anchored, lexical=lexical, resolved=resolved)
