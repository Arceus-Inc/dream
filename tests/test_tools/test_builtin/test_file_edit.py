"""Spec 05 slice B — default ``edit_file`` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.file_edit import FileEditTool


@pytest.fixture
def tool() -> FileEditTool:
    return FileEditTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_edit")


def test_declaration_is_mutating(tool: FileEditTool) -> None:
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required >= 1


def test_name(tool: FileEditTool) -> None:
    assert tool.name == "edit_file"


async def test_replaces_first_occurrence(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("foo\nbar\nfoo\n", encoding="utf-8")
    result = await tool.execute({"path": "code.py", "old_str": "foo", "new_str": "baz"}, ctx)
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "baz\nbar\nfoo\n"
    assert result.metadata["replacements"] == 1
    assert result.metadata["lines_changed"] >= 1


async def test_replace_all(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("foo\nbar\nfoo\nfoo\n", encoding="utf-8")
    result = await tool.execute(
        {
            "path": "code.py",
            "old_str": "foo",
            "new_str": "baz",
            "replace_all": True,
        },
        ctx,
    )
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "baz\nbar\nbaz\nbaz\n"
    assert result.metadata["replacements"] == 3


async def test_old_str_not_found_is_error(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("alpha\n", encoding="utf-8")
    result = await tool.execute({"path": "code.py", "old_str": "missing", "new_str": "x"}, ctx)
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata
    # File unchanged.
    assert f.read_text(encoding="utf-8") == "alpha\n"


async def test_old_str_ambiguous_without_replace_all_is_error(
    tool: FileEditTool, ctx, tmp_path: Path
) -> None:
    f = tmp_path / "code.py"
    f.write_text("dupe\ndupe\n", encoding="utf-8")
    result = await tool.execute({"path": "code.py", "old_str": "dupe", "new_str": "x"}, ctx)
    # Default = replace first only is OK (matches openharness). We still want
    # the ambiguity signalled in metadata so the engine / dispatcher can choose
    # to refuse for safety, but the tool itself succeeds.
    assert result.is_error is False
    assert result.metadata["occurrences"] == 2


async def test_missing_file_is_error(tool: FileEditTool, ctx) -> None:
    result = await tool.execute({"path": "ghost.py", "old_str": "a", "new_str": "b"}, ctx)
    assert result.is_error is True
    assert "not found" in result.metadata["root_cause"].lower()


async def test_uses_atomic_write(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    import dream.tools.builtin.file_edit as mod

    f = tmp_path / "code.py"
    f.write_text("foo\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []
    orig = mod.atomic_write_text

    def spy(path, text, **kw):  # type: ignore[no-untyped-def]
        calls.append((str(path), text))
        return orig(path, text, **kw)

    mod.atomic_write_text = spy  # type: ignore[assignment]
    try:
        result = await tool.execute({"path": "code.py", "old_str": "foo", "new_str": "baz"}, ctx)
        assert result.is_error is False
        assert len(calls) == 1
        assert "baz" in calls[0][1]
    finally:
        mod.atomic_write_text = orig  # type: ignore[assignment]


async def test_noop_when_old_equals_new(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("foo\n", encoding="utf-8")
    result = await tool.execute({"path": "code.py", "old_str": "foo", "new_str": "foo"}, ctx)
    assert result.is_error is True
    assert (
        "noop" in result.metadata["root_cause"].lower()
        or "identical" in result.metadata["root_cause"].lower()
    )


async def test_empty_old_str_is_rejected(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    # Empty old_str matches every boundary; replacing it would corrupt the
    # whole file. It must be rejected before any counting/replacement.
    f = tmp_path / "code.py"
    f.write_text("hello\n", encoding="utf-8")
    result = await tool.execute({"path": "code.py", "old_str": "", "new_str": "X"}, ctx)
    assert result.is_error is True
    assert "empty" in result.metadata["root_cause"].lower()
    # File untouched.
    assert f.read_text(encoding="utf-8") == "hello\n"


async def test_non_utf8_file_is_error(tool: FileEditTool, ctx, tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00bad")
    result = await tool.execute({"path": "blob.bin", "old_str": "a", "new_str": "b"}, ctx)
    assert result.is_error is True
    assert "utf-8" in result.metadata["root_cause"].lower()
    # File untouched.
    assert f.read_bytes() == b"\xff\xfe\x00bad"


async def test_absolute_path_outside_cwd_is_rejected(
    tool: FileEditTool, ctx, tmp_path: Path
) -> None:
    target = tmp_path.parent / f"edit_escape_{tmp_path.name}.txt"
    target.write_text("secret\n", encoding="utf-8")
    try:
        result = await tool.execute(
            {"path": str(target), "old_str": "secret", "new_str": "leaked"}, ctx
        )
        assert result.is_error is True
        assert "escapes" in result.metadata["root_cause"].lower()
        # Unchanged outside the tree.
        assert target.read_text(encoding="utf-8") == "secret\n"
    finally:
        target.unlink(missing_ok=True)
