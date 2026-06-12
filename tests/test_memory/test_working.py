"""Tests for the working-memory scratchpad (spec 11a; :mod:`dream.memory._working`).

The 50 KB cap, in-place compression, history append, and the garbage-output
rollback guard are the load-bearing invariants — a bad compression must never
lose the agent's notes.
"""

from __future__ import annotations

from pathlib import Path

from dream.memory import CompressionOutcome, WorkingMemory


def _wm(tmp_path: Path, *, cap_bytes: int = 50_000) -> WorkingMemory:
    return WorkingMemory(tmp_path / "sidecar" / "working-memory.md", cap_bytes=cap_bytes)


# --- read / write / append -------------------------------------------------


def test_working_memory_read_empty_when_absent(tmp_path: Path) -> None:
    assert _wm(tmp_path).read() == ""


def test_working_memory_read_write_roundtrip(tmp_path: Path) -> None:
    wm = _wm(tmp_path)
    wm.write("what I figured out: the bug is in the retry loop")
    assert wm.read() == "what I figured out: the bug is in the retry loop"


def test_working_memory_write_replaces(tmp_path: Path) -> None:
    wm = _wm(tmp_path)
    wm.write("first")
    wm.write("second")
    assert wm.read() == "second"


def test_working_memory_append_accumulates(tmp_path: Path) -> None:
    wm = _wm(tmp_path)
    wm.append("line one")
    wm.append("line two")
    assert wm.read() == "line one\nline two\n"


def test_working_memory_append_to_empty(tmp_path: Path) -> None:
    wm = _wm(tmp_path)
    wm.append("only line")
    assert wm.read() == "only line\n"


# --- size / cap ------------------------------------------------------------


def test_working_memory_size_and_over_cap(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    assert wm.size_bytes() == 0
    assert wm.over_cap() is False
    wm.write("x" * 20)
    assert wm.size_bytes() == 20
    assert wm.over_cap() is True


def test_history_path_is_alongside(tmp_path: Path) -> None:
    wm = _wm(tmp_path)
    assert wm.history_path.name == "working-memory.history.md"
    assert wm.history_path.parent == wm.path.parent


# --- compression -----------------------------------------------------------


async def test_compression_under_cap_skips(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=1_000)
    wm.write("small")

    async def _compress(_: str) -> str:  # pragma: no cover - must not be called
        raise AssertionError("compressor must not run under the cap")

    outcome = await wm.maybe_compress(_compress)
    assert outcome.status == "skipped"
    assert wm.read() == "small"


async def test_compression_absent_file_skips(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)

    async def _compress(_: str) -> str:  # pragma: no cover - must not be called
        raise AssertionError("compressor must not run on an absent file")

    outcome = await wm.maybe_compress(_compress)
    assert outcome.status == "skipped"
    assert outcome.before_bytes == 0


async def test_working_memory_size_triggers_compression(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    wm.write("x" * 50)

    async def _compress(_: str) -> str:
        return "tiny"

    outcome = await wm.maybe_compress(_compress)
    assert outcome.status == "compressed"


async def test_compression_replaces_file_in_place(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    wm.write("verbose notes " * 10)

    async def _compress(_: str) -> str:
        return "summary"

    await wm.maybe_compress(_compress)
    assert wm.read() == "summary"


async def test_compression_history_appended(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    original = "the full original notes " * 5
    wm.write(original)

    async def _compress(_: str) -> str:
        return "short"

    await wm.maybe_compress(_compress)
    history = wm.history_path.read_text(encoding="utf-8")
    assert original in history
    assert "compressed" in history


async def test_compression_outcome_reports_bytes_and_status(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    wm.write("x" * 100)

    async def _compress(_: str) -> str:
        return "y" * 5

    outcome = await wm.maybe_compress(_compress)
    assert isinstance(outcome, CompressionOutcome)
    assert outcome.before_bytes == 100
    assert outcome.after_bytes == 5
    assert outcome.before_bytes > outcome.after_bytes


async def test_compression_garbage_larger_rolls_back(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    original = "x" * 50
    wm.write(original)

    async def _compress(_: str) -> str:
        return "y" * 100  # larger than the original — garbage

    outcome = await wm.maybe_compress(_compress)
    assert outcome.status == "rolled_back"
    assert wm.read() == original
    assert wm.history_path.exists() is False


async def test_compression_garbage_empty_rolls_back(tmp_path: Path) -> None:
    wm = _wm(tmp_path, cap_bytes=10)
    original = "x" * 50
    wm.write(original)

    async def _compress(_: str) -> str:
        return "   \n  "  # whitespace-only — garbage

    outcome = await wm.maybe_compress(_compress)
    assert outcome.status == "rolled_back"
    assert wm.read() == original
