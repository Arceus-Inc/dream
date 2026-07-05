"""Default ``lsp`` tool — read-only AST code intelligence (tier 0)."""

from __future__ import annotations

from pathlib import Path

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.lsp import LspTool

_SRC = '''\
class Widget:
    """A widget."""

    def render(self) -> str:
        return "w"


def build() -> Widget:
    return Widget()
'''


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def _seed(tmp_path: Path) -> None:
    (tmp_path / "widget.py").write_text(_SRC, encoding="utf-8")
    (tmp_path / "use.py").write_text("from widget import Widget\nw = Widget()\n", encoding="utf-8")


def test_lsp_is_read_only_tier_0() -> None:
    tool = LspTool()
    assert tool.name == "lsp"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


async def test_lsp_document_symbol(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await LspTool().execute(
        {"operation": "document_symbol", "file_path": "widget.py"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "class Widget" in result.content
    assert "Widget.render" in result.content


async def test_lsp_workspace_symbol(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await LspTool().execute(
        {"operation": "workspace_symbol", "query": "widget"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "Widget" in result.content
    assert result.metadata.get("result_count", 0) >= 1


async def test_lsp_go_to_definition(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await LspTool().execute(
        {"operation": "go_to_definition", "file_path": "use.py", "symbol": "Widget"},
        _ctx(tmp_path),
    )
    assert result.is_error is False
    assert "widget.py:1" in result.content


async def test_lsp_find_references(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await LspTool().execute(
        {"operation": "find_references", "file_path": "widget.py", "symbol": "Widget"},
        _ctx(tmp_path),
    )
    assert result.is_error is False
    assert "use.py:" in result.content


async def test_lsp_hover(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await LspTool().execute(
        {"operation": "hover", "file_path": "widget.py", "symbol": "Widget"},
        _ctx(tmp_path),
    )
    assert result.is_error is False
    assert "class Widget" in result.content


async def test_lsp_missing_file_is_error(tmp_path: Path) -> None:
    result = await LspTool().execute(
        {"operation": "document_symbol", "file_path": "nope.py"}, _ctx(tmp_path)
    )
    assert result.is_error is True


async def test_lsp_non_python_is_error(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    result = await LspTool().execute(
        {"operation": "document_symbol", "file_path": "a.txt"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "python" in result.content.lower()


async def test_lsp_path_escape_is_error(tmp_path: Path) -> None:
    result = await LspTool().execute(
        {"operation": "document_symbol", "file_path": "../../etc/hosts"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "outside the working directory" in result.content.lower()
