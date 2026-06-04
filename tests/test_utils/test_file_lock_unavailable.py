"""Spec 01 decision 10: on an unsupported platform, ``exclusive_file_lock``
*raises* rather than silently degrading. Silent degradation is the bug this
contract exists to prevent — a "lock" that does nothing on some OS would make
every read-modify-write call site racy in a way that only shows up in
production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.utils.file_lock import LockError, LockUnavailableError, exclusive_file_lock


def test_unsupported_os_raises_lock_unavailable(tmp_path: Path) -> None:
    with pytest.raises(LockUnavailableError):
        with exclusive_file_lock(tmp_path / "x.lock", os_name="plan9"):
            pass


def test_lock_unavailable_is_lock_error() -> None:
    """Callers catching ``LockError`` must also catch unavailable-platform failures."""
    assert issubclass(LockUnavailableError, LockError)
