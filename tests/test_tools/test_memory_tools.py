"""Default ``memory_search`` and ``memory_get`` tools — spec 11 read surface.

Both are read-only tier-0 inspections of the per-session memory store. The
store rides the ``ToolExecutionContext.metadata`` channel via a
:class:`MemoryContext`; a missing context degrades gracefully (memory is
advisory), while a missing record id is the caller's mistake and surfaces the
Spec 05 three-part error contract.
"""

from __future__ import annotations

from pathlib import Path

from dream.memory import FileMemoryStore, MemoryContext, put_memory_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.memory_get import MemoryGetTool
from dream.tools.builtin.memory_search import MemorySearchTool

_PREFERS_UV = """\
---
name: prefers-uv
description: developer uses uv for everything python
metadata:
  type: user
  scope: project
---

Use `uv run pytest`, never bare pytest.
"""

_NAMING = """\
---
name: naming-convention
description: services are named with a service- prefix
metadata:
  type: project
  scope: project
---

All microservices in this repo are named `service-<domain>`, e.g. service-billing.
"""


def _store(tmp_path: Path) -> FileMemoryStore:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "prefers-uv.md").write_text(_PREFERS_UV, encoding="utf-8")
    (root / "naming-convention.md").write_text(_NAMING, encoding="utf-8")
    return FileMemoryStore(root)


def _ctx(working_dir: Path, store: FileMemoryStore | None) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if store is not None:
        put_memory_context(metadata, MemoryContext(store=store))
    return ToolExecutionContext(
        working_dir=working_dir, session_id="s_test", metadata=metadata
    )


# --- declarations ----------------------------------------------------------


def test_memory_search_is_read_only_tier_0() -> None:
    tool = MemorySearchTool()
    assert tool.name == "memory_search"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_memory_get_is_read_only_tier_0() -> None:
    tool = MemoryGetTool()
    assert tool.name == "memory_get"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


# --- memory_search ---------------------------------------------------------


async def test_memory_search_returns_matching_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = await MemorySearchTool().execute(
        {"query": "naming service"}, _ctx(tmp_path, store)
    )
    assert result.is_error is False
    assert "naming-convention" in result.content
    assert "service- prefix" in result.content
    assert result.metadata.get("hit_count") == 1


async def test_memory_search_empty_store_is_graceful(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = await MemorySearchTool().execute(
        {"query": "zzz-nothing-here"}, _ctx(tmp_path, store)
    )
    assert result.is_error is False
    assert result.metadata.get("hit_count") == 0
    assert "no" in result.content.lower()


async def test_memory_search_no_context_is_graceful(tmp_path: Path) -> None:
    result = await MemorySearchTool().execute(
        {"query": "anything"}, _ctx(tmp_path, None)
    )
    assert result.is_error is False
    assert "not available" in result.content.lower()
    assert result.metadata.get("hit_count") == 0


# --- memory_get ------------------------------------------------------------


async def test_memory_get_returns_full_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = await MemoryGetTool().execute(
        {"id": "naming-convention"}, _ctx(tmp_path, store)
    )
    assert result.is_error is False
    assert "service-<domain>" in result.content
    assert result.metadata.get("id") == "naming-convention"


async def test_memory_get_unknown_id_is_structured_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = await MemoryGetTool().execute({"id": "ghost"}, _ctx(tmp_path, store))
    assert result.is_error is True
    assert "ghost" in result.content
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_memory_get_no_context_is_graceful(tmp_path: Path) -> None:
    result = await MemoryGetTool().execute({"id": "prefers-uv"}, _ctx(tmp_path, None))
    assert result.is_error is False
    assert "not available" in result.content.lower()
