"""Spec 01 — cross-platform exclusive file lock.

Serialises read-modify-write on shared registries. Must auto-release on context
exit, and must *raise* (never silently no-op) on a platform where locking is
unavailable.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from dream.utils.file_lock import LockUnavailableError, exclusive_file_lock


def test_lock_acquire_and_release(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with exclusive_file_lock(lock):
        pass
    # Re-acquiring after release must succeed.
    with exclusive_file_lock(lock):
        pass


def test_lock_creates_parent_dir(tmp_path: Path) -> None:
    lock = tmp_path / "nested" / "dir" / "x.lock"
    with exclusive_file_lock(lock):
        assert lock.parent.is_dir()


def test_lock_serializes_concurrent_writers(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    order: list[str] = []
    a_holds = threading.Event()

    def worker_a() -> None:
        with exclusive_file_lock(lock):
            a_holds.set()
            order.append("a-start")
            time.sleep(0.2)
            order.append("a-end")

    def worker_b() -> None:
        a_holds.wait()
        with exclusive_file_lock(lock):
            order.append("b-start")
            order.append("b-end")

    ta = threading.Thread(target=worker_a)
    tb = threading.Thread(target=worker_b)
    ta.start()
    tb.start()
    ta.join()
    tb.join()

    # b could not enter its critical section until a fully exited.
    assert order == ["a-start", "a-end", "b-start", "b-end"]


def test_lock_raises_on_unsupported_platform(tmp_path: Path) -> None:
    with pytest.raises(LockUnavailableError):
        with exclusive_file_lock(tmp_path / "x.lock", platform="plan9"):
            pass
