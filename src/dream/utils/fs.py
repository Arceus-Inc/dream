"""Atomic file-write helpers (spec 01).

Every harness-initiated write goes through here: write to a same-directory temp
file, fsync it, then ``os.replace`` it over the destination. ``os.replace`` is
atomic on POSIX and (since Python 3.3) Windows, so a concurrent reader sees either
the old file or the new one, never a half-written one. A crash before the rename
leaves the destination untouched and an orphan ``{name}.tmp.{uuid}`` that
``clean_orphan_temp_files`` sweeps at task start.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "clean_orphan_temp_files"]

_TMP_GLOB = "*.tmp.*"


def atomic_write_bytes(
    path: str | os.PathLike[str], data: bytes, *, mode: int | None = None
) -> None:
    """Write ``data`` to ``path`` atomically (temp -> fsync -> rename)."""
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp.{uuid.uuid4().hex}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(tmp, mode)
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Text variant of :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def clean_orphan_temp_files(directory: str | os.PathLike[str]) -> list[Path]:
    """Remove leftover ``*.tmp.*`` files from interrupted writes; return removed paths."""
    d = Path(directory)
    removed: list[Path] = []
    if not d.is_dir():
        return removed
    for p in sorted(d.glob(_TMP_GLOB)):
        with contextlib.suppress(OSError):
            p.unlink()
            removed.append(p)
    return removed


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so the rename is durable (POSIX only; no-op elsewhere)."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
