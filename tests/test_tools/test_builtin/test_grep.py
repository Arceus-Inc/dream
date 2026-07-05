"""Default ``grep`` tool — regex content search (ripgrep or Python fallback).

Read-only (tier 0). Assertions check membership rather than exact ordering so
they hold whether ripgrep or the Python fallback served the search. A missing
root, an out-of-tree path, and an invalid regex all surface the Spec 05
three-part error contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import grep as grep_mod
from dream.tools.builtin.grep import GrepTool


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def _seed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import os\nHELLO = 1\nprint(hello)\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing here\nhello world\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("def hello():\n    return 42\n", encoding="utf-8")


def test_grep_is_read_only_tier_0() -> None:
    tool = GrepTool()
    assert tool.name == "grep"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


async def test_grep_finds_matches_with_line_numbers(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": r"def hello"}, _ctx(tmp_path))
    assert result.is_error is False
    assert "sub/c.py:1:def hello():" in result.content
    assert result.metadata.get("match_count") == 1


async def test_grep_case_insensitive(tmp_path: Path) -> None:
    _seed(tmp_path)
    sensitive = await GrepTool().execute({"pattern": "hello"}, _ctx(tmp_path))
    insensitive = await GrepTool().execute(
        {"pattern": "hello", "case_sensitive": False}, _ctx(tmp_path)
    )
    # 'HELLO' only shows up when case-insensitive.
    assert "a.py:2:HELLO = 1" not in sensitive.content
    assert "a.py:2:HELLO = 1" in insensitive.content


async def test_grep_glob_filter(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GrepTool().execute(
        {"pattern": "hello", "glob": "*.txt"}, _ctx(tmp_path)
    )
    assert "b.txt:2:hello world" in result.content
    assert ".py:" not in result.content


async def test_grep_no_matches_is_graceful(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "zzz-not-here"}, _ctx(tmp_path))
    assert result.is_error is False
    assert result.metadata.get("match_count") == 0
    assert "no matches" in result.content.lower()


async def test_grep_single_file_root(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GrepTool().execute(
        {"pattern": "HELLO", "path": "a.py"}, _ctx(tmp_path)
    )
    assert "a.py:2:HELLO = 1" in result.content


async def test_grep_missing_root_is_error(tmp_path: Path) -> None:
    result = await GrepTool().execute(
        {"pattern": "x", "path": "nope"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert result.metadata.get("root_cause")


async def test_grep_path_escape_is_error(tmp_path: Path) -> None:
    result = await GrepTool().execute(
        {"pattern": "x", "path": "../../etc"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "outside the working directory" in result.content.lower()


async def test_grep_invalid_regex_is_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "("}, _ctx(tmp_path))
    assert result.is_error is True
    assert result.metadata.get("root_cause")


async def test_grep_python_fallback_when_no_ripgrep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(grep_mod.shutil, "which", lambda _name: None)
    result = await GrepTool().execute({"pattern": r"def hello"}, _ctx(tmp_path))
    assert result.is_error is False
    assert "sub/c.py:1:def hello():" in result.content
