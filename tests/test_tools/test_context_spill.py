"""Unit tests for ToolExecutionContext.spill_large_output (dream.tools._context).

Covers the spill path (scratch_dir wired and unwired), bytes → text
conversion, and the inline-vs-offloaded decision.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext, _compose_subprocess_result

# --- spill_large_output (lines 111-133) ---


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return ToolExecutionContext(
        working_dir=tmp_path, session_id="s_spill", scratch_dir=scratch
    )


@pytest.mark.asyncio
async def test_spill_small_content_returns_inline(ctx: ToolExecutionContext) -> None:
    result = await ctx.spill_large_output("short text")
    assert result == "short text"


@pytest.mark.asyncio
async def test_spill_bytes_decoded_to_utf8(ctx: ToolExecutionContext) -> None:
    result = await ctx.spill_large_output(b"byte content")
    assert result == "byte content"


@pytest.mark.asyncio
async def test_spill_bytes_with_invalid_utf8(ctx: ToolExecutionContext) -> None:
    result = await ctx.spill_large_output(b"\xff\xfe bad bytes")
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_spill_large_content_returns_filename(ctx: ToolExecutionContext) -> None:
    large = "x" * 200_000
    result = await ctx.spill_large_output(large)
    # When offloaded, the result is a filename pointing to the scratch dir.
    assert result != large


@pytest.mark.asyncio
async def test_spill_without_scratch_dir_uses_default(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_no_scratch")
    result = await ctx.spill_large_output("inline content")
    assert result == "inline content"


@pytest.mark.asyncio
async def test_spill_without_scratch_dir_creates_default_on_large(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_no_scratch2")
    large = "x" * 200_000
    result = await ctx.spill_large_output(large)
    default_scratch = tmp_path / ".dream" / "scratch"
    assert default_scratch.exists()
    assert result != large


# --- _compose_subprocess_result (lines 136-165) ---


def test_compose_result_success() -> None:
    result = _compose_subprocess_result(b"out", b"", 0)
    assert not result.is_error
    assert result.content == "out"
    assert result.metadata["returncode"] == 0
    assert result.metadata["stdout_bytes"] == 3
    assert result.metadata["stderr_bytes"] == 0


def test_compose_result_with_stderr_only() -> None:
    result = _compose_subprocess_result(b"", b"err msg", 1)
    assert result.is_error
    assert result.content == "err msg"
    assert "root_cause" in result.metadata


def test_compose_result_with_both_streams() -> None:
    result = _compose_subprocess_result(b"out", b"err", 1)
    assert result.is_error
    assert "out" in result.content
    assert "stderr" in result.content
    assert "err" in result.content


def test_compose_result_error_metadata() -> None:
    result = _compose_subprocess_result(b"", b"fail", 2)
    assert result.metadata["root_cause"] == "exit code 2"
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


def test_compose_result_none_returncode() -> None:
    result = _compose_subprocess_result(b"out", b"", None)
    assert result.is_error
    assert result.metadata["returncode"] is None


def test_compose_result_utf8_replacement() -> None:
    result = _compose_subprocess_result(b"\xff\xfe", b"", 0)
    assert isinstance(result.content, str)
