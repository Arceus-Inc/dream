"""Cross-platform exclusive file lock (spec 01).

Serialises read-modify-write on shared registries (``state.json``, cron, memory
index, swarm mailbox). POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``.
The lock auto-releases on context exit and on process exit (the OS drops it when
the fd closes). An unsupported platform *raises* ``LockUnavailableError`` rather
than silently degrading. Pair with :func:`dream.utils.fs.atomic_write_text` to be
both race-free and crash-safe.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path

__all__ = ["LockError", "LockUnavailableError", "exclusive_file_lock"]


class LockError(RuntimeError):
    """Base error for file-lock failures."""


class LockUnavailableError(LockError):
    """Raised when file locking is unavailable on the current platform."""


class _LockBackend(Enum):
    """Which OS locking primitive to use. Replaces stringly-typed dispatch."""

    POSIX = auto()
    WINDOWS = auto()

    @classmethod
    def for_os(cls, os_name: str) -> _LockBackend:
        """Map ``os.name`` onto a backend; raise on anything unsupported."""
        if os_name == "posix":
            return cls.POSIX
        if os_name == "nt":
            return cls.WINDOWS
        raise LockUnavailableError(f"file locking is not supported on os.name={os_name!r}")


@contextmanager
def exclusive_file_lock(
    lock_path: str | Path,
    *,
    os_name: str | None = None,
) -> Iterator[None]:
    """Hold an OS-level exclusive lock on ``lock_path`` for the context body.

    ``os_name`` defaults to :data:`os.name`; pass it explicitly to exercise a
    specific backend (or an unsupported one) in tests.
    """
    backend = _LockBackend.for_os(os.name if os_name is None else os_name)
    path = Path(lock_path)
    if backend is _LockBackend.WINDOWS:  # pragma: no cover - not exercised on posix CI
        with _windows_lock(path):
            yield
    else:
        with _posix_lock(path):
            yield


@contextmanager
def _posix_lock(lock_path: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - posix always has fcntl
        raise LockUnavailableError(f"fcntl not available: {exc}") from exc

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def _windows_lock(
    lock_path: Path,
) -> Iterator[None]:  # pragma: no cover - not exercised on posix CI
    try:
        import msvcrt
    except ImportError as exc:
        raise LockUnavailableError(f"msvcrt not available: {exc}") from exc

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as fh:
        fh.seek(0)
        if lock_path.stat().st_size == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
