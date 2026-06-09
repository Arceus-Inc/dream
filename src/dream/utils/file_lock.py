"""Cross-platform exclusive file lock (spec 01).

Serialises read-modify-write on shared registries (``state.json``, cron, memory
index, swarm mailbox). POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``.
The lock auto-releases on context exit and on process exit (the OS drops it when
the fd closes). An unsupported platform *raises* ``LockUnavailableError`` rather
than silently degrading. Pair with :func:`dream.utils.fs.atomic_write_text` to be
both race-free and crash-safe.

**Caveat — networked filesystems.** ``fcntl.flock`` is advisory and may be a
no-op on NFS / SMB / some bind mounts, where serialisation silently degrades.
Keep ``.dream`` on a local filesystem for the lock guarantees to hold.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path

__all__ = [
    "LockError",
    "LockUnavailableError",
    "exclusive_file_lock",
    "try_exclusive_file_lock",
]


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
        with _windows_lock(path, blocking=True):
            yield
    else:
        with _posix_lock(path, blocking=True):
            yield


@contextmanager
def _posix_lock(lock_path: Path, *, blocking: bool) -> Iterator[bool]:
    """POSIX ``flock`` scaffolding shared by the blocking and non-blocking paths.

    Always yields whether the lock was acquired: ``blocking=True`` only ever
    yields ``True`` (it waits), while ``blocking=False`` yields ``False`` without
    entering the critical section when the lock is held elsewhere.
    """
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - posix always has fcntl
        raise LockUnavailableError(f"fcntl not available: {exc}") from exc

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+b") as fh:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError:  # only reachable when non-blocking
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def _windows_lock(  # pragma: no cover - not exercised on posix CI
    lock_path: Path, *, blocking: bool
) -> Iterator[bool]:
    """Windows ``msvcrt.locking`` scaffolding shared by both paths.

    Mirrors :func:`_posix_lock`: ``blocking=True`` retries until acquired (LK_LOCK
    gives up after ~10s and raises ``OSError``), ``blocking=False`` (LK_NBLCK)
    yields ``False`` immediately when the lock is held elsewhere.
    """
    try:
        import msvcrt
    except ImportError as exc:
        raise LockUnavailableError(f"msvcrt not available: {exc}") from exc

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as fh:
        fh.seek(0)
        if os.fstat(fh.fileno()).st_size == 0:  # stat the open fd, not the path (TOCTOU)
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        if blocking:
            # LK_LOCK gives up after ~10s and raises OSError; retry so a long
            # critical section blocks until acquired (parity with POSIX LOCK_EX).
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                yield False
                return
        try:
            yield True
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]


@contextmanager
def try_exclusive_file_lock(
    lock_path: str | Path,
    *,
    os_name: str | None = None,
) -> Iterator[bool]:
    """Non-blocking variant of :func:`exclusive_file_lock`.

    Yields ``True`` if the lock was acquired (auto-released on exit),
    ``False`` if it is currently held elsewhere. Callers branch on the
    yielded value::

        with try_exclusive_file_lock(p) as acquired:
            if not acquired:
                return WakeOutcome(dropped_reason="heartbeat_in_flight")
            ...

    Used by the spec 06.5 slice 2 wake-cycle orchestrator to dedup
    overlapping wakes for the same agent.
    """
    backend = _LockBackend.for_os(os.name if os_name is None else os_name)
    path = Path(lock_path)
    if backend is _LockBackend.WINDOWS:  # pragma: no cover - not exercised on posix CI
        with _windows_lock(path, blocking=False) as acquired:
            yield acquired
    else:
        with _posix_lock(path, blocking=False) as acquired:
            yield acquired
