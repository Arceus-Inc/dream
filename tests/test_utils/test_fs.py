"""Spec 01 — atomic writes (temp -> fsync -> rename) and orphan-temp cleanup.

Invariant: a reader never observes a torn file, and a crash mid-write leaves the
previous version fully intact. Temp files match the spec pattern `{name}.tmp.{uuid}`.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from dream.utils.fs import atomic_write_bytes, atomic_write_text, clean_orphan_temp_files


def test_atomic_write_bytes_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "héllo")
    assert target.read_text(encoding="utf-8") == "héllo"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    assert target.read_text() == "v2"


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.txt"
    atomic_write_text(target, "x")
    assert target.read_text() == "x"


def test_no_temp_file_left_after_success(tmp_path: Path) -> None:
    atomic_write_text(tmp_path / "f.txt", "x")
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_failure_at_rename_preserves_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "v1")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("dream.utils.fs.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "v2")

    assert target.read_text() == "v1"  # old version intact
    assert list(tmp_path.glob("*.tmp.*")) == []  # temp cleaned by error path


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_atomic_write_mode_applied(tmp_path: Path) -> None:
    target = tmp_path / "secret"
    atomic_write_bytes(target, b"x", mode=0o600)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_clean_orphan_temp_files_removes_only_scheme_matches(tmp_path: Path) -> None:
    real = tmp_path / "state.json"
    real.write_text("{}")
    # Contains ".tmp." but is NOT our {name}.tmp.{32-hex} scheme — must survive.
    not_ours = tmp_path / "report.tmp.backup"
    not_ours.write_text("keep me")
    orphan = tmp_path / ("state.json.tmp." + "a" * 32)  # exact scheme: 32 hex
    orphan.write_text("partial")

    removed = clean_orphan_temp_files(tmp_path)

    assert not orphan.exists()
    assert real.read_text() == "{}"
    assert not_ours.read_text() == "keep me"  # broad match would have deleted it
    assert removed == [orphan]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_explicit_mode_applied_at_creation(tmp_path: Path) -> None:
    target = tmp_path / "secret"
    atomic_write_bytes(target, b"x", mode=0o600)
    assert target.read_bytes() == b"x"
    # Mode is applied when the temp file is created (no world-readable window),
    # so the destination carries exactly the requested permission bits.
    assert (target.stat().st_mode & 0o777) == 0o600


def test_explicit_mode_failure_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "secret"

    def boom(*_a: object, **_k: object) -> int:
        raise OSError("open denied")

    # The mode path creates the temp via os.open; a failure there must surface
    # (the write must not report success) and leave no temp behind.
    monkeypatch.setattr("dream.utils.fs.os.open", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"x", mode=0o600)

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp.*")) == []  # temp cleaned by the error path


def test_clean_orphan_temp_files_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert clean_orphan_temp_files(tmp_path) == []


def test_clean_orphan_temp_files_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert clean_orphan_temp_files(tmp_path / "nope") == []
