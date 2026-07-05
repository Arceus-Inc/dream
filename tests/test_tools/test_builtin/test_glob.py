"""Default ``glob`` tool — file listing by glob (ripgrep walker or Python fallback).

Read-only (tier 0). Assertions check membership so they hold under either
backend. An out-of-tree or non-directory root surfaces the Spec 05 error contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import glob as glob_mod
from dream.tools.builtin.glob import GlobTool


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def _seed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# notes\n", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "b.py").write_text("y = 2\n", encoding="utf-8")
    (pkg / "c.py").write_text("z = 3\n", encoding="utf-8")


def test_glob_is_read_only_tier_0() -> None:
    tool = GlobTool()
    assert tool.name == "glob"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


async def test_glob_recursive_python_files(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GlobTool().execute({"pattern": "**/*.py"}, _ctx(tmp_path))
    assert result.is_error is False
    lines = set(result.content.splitlines())
    assert "a.py" in lines
    assert "pkg/b.py" in lines
    assert "pkg/c.py" in lines
    assert "notes.md" not in lines
    assert result.metadata.get("match_count") == 3


async def test_glob_shallow_pattern(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GlobTool().execute({"pattern": "*.md"}, _ctx(tmp_path))
    assert result.content.splitlines() == ["notes.md"]


async def test_glob_subdir_root(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GlobTool().execute(
        {"pattern": "**/*.py", "path": "pkg"}, _ctx(tmp_path)
    )
    lines = set(result.content.splitlines())
    assert lines == {"b.py", "c.py"}


async def test_glob_no_matches_is_graceful(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GlobTool().execute({"pattern": "**/*.rs"}, _ctx(tmp_path))
    assert result.is_error is False
    assert result.metadata.get("match_count") == 0
    assert "no matches" in result.content.lower()


async def test_glob_path_escape_is_error(tmp_path: Path) -> None:
    result = await GlobTool().execute(
        {"pattern": "*", "path": "../.."}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "outside the working directory" in result.content.lower()


async def test_glob_non_directory_root_is_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await GlobTool().execute(
        {"pattern": "*", "path": "a.py"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert result.metadata.get("root_cause")


async def test_glob_python_fallback_when_no_ripgrep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(glob_mod.shutil, "which", lambda _name: None)
    result = await GlobTool().execute({"pattern": "**/*.py"}, _ctx(tmp_path))
    assert result.is_error is False
    assert set(result.content.splitlines()) == {"a.py", "pkg/b.py", "pkg/c.py"}
