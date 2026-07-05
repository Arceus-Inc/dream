"""Default ``todo_write`` tool — markdown checklist maintenance (mutating, tier 1).

Adds items, flips them to done in place, no-ops when already in the desired
state, seeds a fresh file, and refuses out-of-tree paths (Spec 05 contract).
"""

from __future__ import annotations

from pathlib import Path

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.todo_write import TodoWriteTool


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def test_todo_write_is_mutating_tier_1() -> None:
    tool = TodoWriteTool()
    assert tool.name == "todo_write"
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required == 1
    assert tool.is_read_only() is False


async def test_todo_write_seeds_and_appends(tmp_path: Path) -> None:
    result = await TodoWriteTool().execute({"item": "write tests"}, _ctx(tmp_path))
    assert result.is_error is False
    assert result.metadata.get("changed") is True
    body = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert body.startswith("# TODO")
    assert "- [ ] write tests" in body


async def test_todo_write_checks_existing_item(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await TodoWriteTool().execute({"item": "ship it"}, ctx)
    result = await TodoWriteTool().execute({"item": "ship it", "checked": True}, ctx)
    assert result.is_error is False
    body = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert "- [x] ship it" in body
    assert "- [ ] ship it" not in body


async def test_todo_write_no_op_when_already_in_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await TodoWriteTool().execute({"item": "dup"}, ctx)
    result = await TodoWriteTool().execute({"item": "dup"}, ctx)
    assert result.is_error is False
    assert result.metadata.get("changed") is False
    body = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert body.count("- [ ] dup") == 1


async def test_todo_write_custom_path(tmp_path: Path) -> None:
    result = await TodoWriteTool().execute(
        {"item": "task", "path": "docs/TASKS.md"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert (tmp_path / "docs" / "TASKS.md").exists()


async def test_todo_write_path_escape_is_error(tmp_path: Path) -> None:
    result = await TodoWriteTool().execute(
        {"item": "x", "path": "../evil.md"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "outside the working directory" in result.content.lower()
    assert not (tmp_path.parent / "evil.md").exists()
