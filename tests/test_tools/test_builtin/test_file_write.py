"""Spec 05 slice B — default ``write_file`` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.file_write import FileWriteTool


@pytest.fixture
def tool() -> FileWriteTool:
    return FileWriteTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_write")


def test_declaration_is_mutating(tool: FileWriteTool) -> None:
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required >= 1
    assert tool.is_read_only() is False


def test_name(tool: FileWriteTool) -> None:
    assert tool.name == "write_file"


async def test_creates_file_with_content(tool: FileWriteTool, ctx, tmp_path: Path) -> None:
    result = await tool.execute({"path": "out.txt", "content": "hello world\n"}, ctx)
    assert result.is_error is False
    out = tmp_path / "out.txt"
    assert out.read_text(encoding="utf-8") == "hello world\n"
    assert result.metadata["bytes_written"] == len(b"hello world\n")
    assert str(out) in result.metadata["artifacts"]


async def test_creates_parent_directories(tool: FileWriteTool, ctx, tmp_path: Path) -> None:
    result = await tool.execute({"path": "nested/deep/out.txt", "content": "x"}, ctx)
    assert result.is_error is False
    assert (tmp_path / "nested" / "deep" / "out.txt").read_text() == "x"


async def test_overwrites_existing_file(tool: FileWriteTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "existing.txt"
    f.write_text("old\n", encoding="utf-8")
    result = await tool.execute({"path": "existing.txt", "content": "new\n"}, ctx)
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "new\n"


async def test_uses_atomic_write(tool: FileWriteTool, ctx, tmp_path: Path) -> None:
    """Spec 01 invariant: every harness-initiated write goes through atomic_write_*.

    We verify by patching the helper -- if write_file calls a raw open()/write_text
    on the destination directly, the patch never runs and the file stays missing.
    """
    import dream.tools.builtin.file_write as mod

    calls: list[tuple[str, str]] = []
    orig = mod.atomic_write_text

    def spy(path, text, **kw):  # type: ignore[no-untyped-def]
        calls.append((str(path), text))
        return orig(path, text, **kw)

    mod.atomic_write_text = spy  # type: ignore[assignment]
    try:
        result = await tool.execute({"path": "spy.txt", "content": "atomic"}, ctx)
        assert result.is_error is False
        assert len(calls) == 1
        assert calls[0][1] == "atomic"
    finally:
        mod.atomic_write_text = orig  # type: ignore[assignment]


async def test_write_to_directory_is_error(tool: FileWriteTool, ctx, tmp_path: Path) -> None:
    (tmp_path / "adir").mkdir()
    result = await tool.execute({"path": "adir", "content": "x"}, ctx)
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_absolute_path_outside_cwd_is_rejected(
    tool: FileWriteTool, ctx, tmp_path: Path
) -> None:
    target = tmp_path.parent / f"escape_{tmp_path.name}.txt"
    try:
        result = await tool.execute({"path": str(target), "content": "pwned"}, ctx)
        assert result.is_error is True
        assert "escapes" in result.metadata["root_cause"].lower()
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


async def test_dotdot_traversal_is_rejected(tool: FileWriteTool, ctx, tmp_path: Path) -> None:
    target = tmp_path.parent / f"escape2_{tmp_path.name}.txt"
    try:
        result = await tool.execute({"path": f"../{target.name}", "content": "pwned"}, ctx)
        assert result.is_error is True
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


async def test_atomic_write_failure_returns_structured_error(
    tool: FileWriteTool, ctx, tmp_path: Path
) -> None:
    # A permission/disk-full/invalid-path failure from atomic_write_text must
    # become a structured tool error, not an unhandled OSError.
    import dream.tools.builtin.file_write as mod

    def boom(path, text, **kw):  # type: ignore[no-untyped-def]
        raise PermissionError("denied")

    orig = mod.atomic_write_text
    mod.atomic_write_text = boom  # type: ignore[assignment]
    try:
        result = await tool.execute({"path": "out.txt", "content": "data"}, ctx)
        assert result.is_error is True
        assert "denied" in result.metadata["root_cause"]
        assert "safe_retry" in result.metadata
        assert "stop_condition" in result.metadata
    finally:
        mod.atomic_write_text = orig  # type: ignore[assignment]
