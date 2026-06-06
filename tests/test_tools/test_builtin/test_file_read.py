"""Spec 05 slice B — default ``read_file`` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools.builtin.file_read import FileReadTool


@pytest.fixture
def tool() -> FileReadTool:
    return FileReadTool()


@pytest.fixture
def ctx(tmp_path: Path):
    from dream.tools._context import ToolExecutionContext

    return ToolExecutionContext(working_dir=tmp_path, session_id="s_read")


def test_declaration_is_safe_tier0(tool: FileReadTool) -> None:
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_name_and_schema(tool: FileReadTool) -> None:
    assert tool.name == "read_file"
    schema = tool.input_schema()
    assert "path" in schema["properties"]


async def test_reads_file_with_line_numbers(tool: FileReadTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    result = await tool.execute({"path": "hello.txt"}, ctx)
    assert result.is_error is False
    assert "1\talpha" in result.content
    assert "3\tgamma" in result.content
    assert result.metadata["lines_returned"] == 3


async def test_reads_with_offset_and_limit(tool: FileReadTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "long.txt"
    f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    result = await tool.execute({"path": "long.txt", "offset": 2, "limit": 3}, ctx)
    assert result.is_error is False
    assert "3\tline2" in result.content
    assert "5\tline4" in result.content
    assert "6\tline5" not in result.content
    assert result.metadata["lines_returned"] == 3


async def test_missing_file_returns_error_with_3part_contract(tool: FileReadTool, ctx) -> None:
    result = await tool.execute({"path": "no_such_file.txt"}, ctx)
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_directory_is_error(tool: FileReadTool, ctx, tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    result = await tool.execute({"path": "subdir"}, ctx)
    assert result.is_error is True
    assert "directory" in result.metadata["root_cause"].lower()


async def test_binary_file_is_error(tool: FileReadTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "bin.dat"
    f.write_bytes(b"\x00\x01\x02binary\x00")
    result = await tool.execute({"path": "bin.dat"}, ctx)
    assert result.is_error is True
    assert "binary" in result.metadata["root_cause"].lower()


async def test_absolute_path_outside_cwd_still_resolves(
    tool: FileReadTool, ctx, tmp_path: Path
) -> None:
    other = tmp_path.parent / f"outside_{tmp_path.name}.txt"
    other.write_text("outside\n", encoding="utf-8")
    try:
        result = await tool.execute({"path": str(other)}, ctx)
        assert result.is_error is False
        assert "outside" in result.content
    finally:
        other.unlink(missing_ok=True)


async def test_offset_past_eof_returns_empty_range_note(
    tool: FileReadTool, ctx, tmp_path: Path
) -> None:
    f = tmp_path / "short.txt"
    f.write_text("only\n", encoding="utf-8")
    result = await tool.execute({"path": "short.txt", "offset": 999, "limit": 10}, ctx)
    assert result.is_error is False
    assert result.metadata["lines_returned"] == 0
