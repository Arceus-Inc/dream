"""Tests for the task-memory tools (spec 11a).

The three ``working_memory_*`` tools and the outbound ``memory_propose`` seam.
All are safe / tier 0 (task memory is cognition, not a sandboxed repo effect) and
degrade gracefully when no :class:`TaskMemoryContext` is wired.
"""

from __future__ import annotations

from pathlib import Path

from dream.memory import TaskMemoryContext, WorkingMemory, put_task_memory_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.propose_memory import MemoryProposeTool
from dream.tools.builtin.working_memory import (
    WorkingMemoryAppendTool,
    WorkingMemoryReadTool,
    WorkingMemoryWriteTool,
)


def _ctx(tmp_path: Path, *, wired: bool = True, cap_bytes: int = 50_000) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if wired:
        wm = WorkingMemory(tmp_path / "sidecar" / "working-memory.md", cap_bytes=cap_bytes)
        put_task_memory_context(
            metadata,
            TaskMemoryContext(
                working_memory=wm,
                proposals_dir=tmp_path / "home" / "_proposals",
                source_ref="session://s_test",
            ),
        )
    return ToolExecutionContext(
        working_dir=tmp_path, session_id="s_test", metadata=metadata
    )


# --- declarations ----------------------------------------------------------


def test_working_memory_tools_are_safe_tier_0() -> None:
    for tool in (WorkingMemoryReadTool(), WorkingMemoryWriteTool(), WorkingMemoryAppendTool()):
        assert tool.declaration.risk == "safe"
        assert tool.declaration.tier_required == 0
        assert tool.is_read_only() is True


def test_memory_propose_is_safe_tier_0() -> None:
    tool = MemoryProposeTool()
    assert tool.name == "memory_propose"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0


# --- working memory --------------------------------------------------------


async def test_working_memory_write_then_read(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await WorkingMemoryWriteTool().execute({"content": "notes here"}, ctx)
    result = await WorkingMemoryReadTool().execute({}, ctx)
    assert result.is_error is False
    assert "notes here" in result.content


async def test_working_memory_read_empty(tmp_path: Path) -> None:
    result = await WorkingMemoryReadTool().execute({}, _ctx(tmp_path))
    assert result.is_error is False
    assert "empty" in result.content.lower()


async def test_working_memory_append(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await WorkingMemoryAppendTool().execute({"note": "first"}, ctx)
    await WorkingMemoryAppendTool().execute({"note": "second"}, ctx)
    result = await WorkingMemoryReadTool().execute({}, ctx)
    assert "first" in result.content
    assert "second" in result.content


async def test_working_memory_write_over_cap_warns(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, cap_bytes=10)
    result = await WorkingMemoryWriteTool().execute({"content": "x" * 50}, ctx)
    assert result.is_error is False
    assert result.metadata.get("warning") is True


async def test_working_memory_write_under_cap_no_warning(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, cap_bytes=1_000)
    result = await WorkingMemoryWriteTool().execute({"content": "small"}, ctx)
    assert result.metadata.get("warning") is None


# --- memory_propose --------------------------------------------------------


async def test_memory_propose_creates_proposal_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    result = await MemoryProposeTool().execute(
        {"slug": "retry-policy", "content": "back off with jitter", "rationale": "recurring"},
        ctx,
    )
    assert result.is_error is False
    artifacts = result.metadata["artifacts"]
    assert isinstance(artifacts, list)
    created = Path(artifacts[0])
    assert created.exists()
    assert created.name.endswith("-retry-policy.md")


async def test_memory_propose_bad_slug_errors(tmp_path: Path) -> None:
    result = await MemoryProposeTool().execute(
        {"slug": "../escape", "content": "x", "rationale": "y"},
        _ctx(tmp_path),
    )
    assert result.is_error is True
    assert result.metadata["root_cause"]
    assert result.metadata["safe_retry"]
    assert result.metadata["stop_condition"]


# --- graceful degradation --------------------------------------------------


async def test_task_memory_tools_degrade_without_context(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, wired=False)
    for tool, args in (
        (WorkingMemoryReadTool(), {}),
        (WorkingMemoryWriteTool(), {"content": "x"}),
        (WorkingMemoryAppendTool(), {"note": "x"}),
        (MemoryProposeTool(), {"slug": "s", "content": "c", "rationale": "r"}),
    ):
        result = await tool.execute(args, ctx)
        assert result.is_error is False
        assert "not available" in result.content.lower()
