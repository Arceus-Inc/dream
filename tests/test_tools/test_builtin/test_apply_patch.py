"""Level-2 ``apply_patch`` tool — Codex multi-hunk edits (replaces edit_file)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import apply_patch as apply_patch_mod
from dream.tools.builtin.apply_patch import ApplyPatchTool


@pytest.fixture
def tool() -> ApplyPatchTool:
    return ApplyPatchTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_patch")


def _update_patch(path: str, *hunk_lines: str) -> str:
    body = "\n".join(hunk_lines)
    return (
        "*** Begin Patch\n"
        f"*** Update File: {path}\n"
        f"{body}\n"
        "*** End Patch"
    )


def test_declaration_is_mutating(tool: ApplyPatchTool) -> None:
    assert tool.name == "apply_patch"
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required >= 1


def test_effects_report_paths(tool: ApplyPatchTool) -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+x\n"
        "*** Update File: old.py\n"
        "@@\n"
        "-a\n"
        "+b\n"
        "*** End Patch"
    )
    eff = tool.effects_for({"patch": patch})
    assert Path("new.py") in eff.target_paths
    assert Path("old.py") in eff.target_paths


async def test_update_single_hunk(tool: ApplyPatchTool, ctx: ToolExecutionContext, tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("foo\nbar\nqux\n", encoding="utf-8")
    result = await tool.execute(
        {
            "patch": _update_patch(
                "code.py",
                "@@",
                "-foo",
                "+baz",
                " bar",
                " qux",
            )
        },
        ctx,
    )
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "baz\nbar\nqux\n"
    assert result.metadata["change_count"] == 1


async def test_multi_hunk_single_file(
    tool: ApplyPatchTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    f = tmp_path / "multi.txt"
    f.write_text("a1\na2\na3\na4\na5\n", encoding="utf-8")
    result = await tool.execute(
        {
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: multi.txt\n"
                "@@\n"
                " a1\n"
                "-a2\n"
                "+A2\n"
                " a3\n"
                " a4\n"
                "-a5\n"
                "+A5\n"
                "*** End Patch"
            )
        },
        ctx,
    )
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "a1\nA2\na3\na4\nA5\n"


async def test_add_and_delete(
    tool: ApplyPatchTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    doomed = tmp_path / "gone.txt"
    doomed.write_text("bye\n", encoding="utf-8")
    result = await tool.execute(
        {
            "patch": (
                "*** Begin Patch\n"
                "*** Add File: hello.txt\n"
                "+hi\n"
                "*** Delete File: gone.txt\n"
                "*** End Patch"
            )
        },
        ctx,
    )
    assert result.is_error is False
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert not doomed.exists()


async def test_missing_context_is_error(
    tool: ApplyPatchTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    f = tmp_path / "code.py"
    f.write_text("alpha\n", encoding="utf-8")
    result = await tool.execute(
        {
            "patch": _update_patch(
                "code.py",
                "@@",
                "-missing",
                "+x",
            )
        },
        ctx,
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert f.read_text(encoding="utf-8") == "alpha\n"


async def test_escape_path_refused(tool: ApplyPatchTool, ctx: ToolExecutionContext) -> None:
    result = await tool.execute(
        {
            "patch": (
                "*** Begin Patch\n"
                "*** Add File: ../outside.txt\n"
                "+nope\n"
                "*** End Patch"
            )
        },
        ctx,
    )
    assert result.is_error is True


async def test_uses_atomic_write(
    tool: ApplyPatchTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    f = tmp_path / "code.py"
    f.write_text("old\n", encoding="utf-8")
    with patch.object(
        apply_patch_mod, "atomic_write_text", wraps=apply_patch_mod.atomic_write_text
    ) as spy:
        result = await tool.execute(
            {
                "patch": _update_patch(
                    "code.py",
                    "@@",
                    "-old",
                    "+new",
                )
            },
            ctx,
        )
    assert result.is_error is False, result.content
    spy.assert_called()


async def test_empty_patch_refused(tool: ApplyPatchTool, ctx: ToolExecutionContext) -> None:
    result = await tool.execute({"patch": "   "}, ctx)
    assert result.is_error is True
    assert "empty" in result.metadata["root_cause"].lower()
