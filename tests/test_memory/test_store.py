"""Memory substrate — read-side file store (spec 11 substrate; spec 15 P4 §4).

Memory is markdown with YAML frontmatter under a per-project directory
(``~/.dream/memory/{project}-{sha}/``). The SDK ships the *read side*
(:class:`dream.contracts.memory.MemoryStore`); curation/evolution stays
an employee in the business repo (Model A).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.contracts.memory import MemoryScope, MemoryStore, MemoryType
from dream.memory import FileMemoryStore, project_memory_dir

_RECORD = """\
---
name: prefers-uv
description: developer uses uv for everything python
metadata:
  type: user
  scope: project
---

Use `uv run pytest`, never bare pytest. [[ci-quirks]]
"""


def _store(tmp_path: Path) -> FileMemoryStore:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "prefers-uv.md").write_text(_RECORD, encoding="utf-8")
    (root / "ci-quirks.md").write_text(
        "---\nname: ci-quirks\ndescription: CI runs mypy strict\n"
        "metadata:\n  type: project\n---\n\nCI gates: ruff, mypy, pytest.\n",
        encoding="utf-8",
    )
    # An index file and a non-record file must not become records.
    (root / "MEMORY.md").write_text("# Index\n- [x](prefers-uv.md)\n", encoding="utf-8")
    (root / "notes.txt").write_text("not memory", encoding="utf-8")
    return FileMemoryStore(root)


@pytest.mark.asyncio
async def test_list_parses_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = await store.list()
    assert sorted(r.id for r in records) == ["ci-quirks", "prefers-uv"]
    by_id = {r.id: r for r in records}
    assert by_id["prefers-uv"].type == MemoryType.USER
    assert by_id["prefers-uv"].scope == MemoryScope.PROJECT
    assert "uv run pytest" in by_id["prefers-uv"].content
    assert by_id["ci-quirks"].type == MemoryType.PROJECT


@pytest.mark.asyncio
async def test_list_filters_by_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = await store.list(type=MemoryType.USER)
    assert [r.id for r in records] == ["prefers-uv"]


@pytest.mark.asyncio
async def test_get_by_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = await store.get("ci-quirks")
    assert record is not None
    assert record.frontmatter["description"] == "CI runs mypy strict"
    assert await store.get("nope") is None


@pytest.mark.asyncio
async def test_search_ranks_matches(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = await store.search("mypy")
    assert [r.id for r in results] == ["ci-quirks"]
    assert await store.search("zzz-nothing") == []


@pytest.mark.asyncio
async def test_corrupt_record_is_skipped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (tmp_path / "memory" / "broken.md").write_text(
        "no frontmatter here", encoding="utf-8"
    )
    records = await store.list()
    assert sorted(r.id for r in records) == ["ci-quirks", "prefers-uv"]


@pytest.mark.asyncio
async def test_missing_dir_is_empty(tmp_path: Path) -> None:
    store = FileMemoryStore(tmp_path / "nowhere")
    assert await store.list() == []
    assert await store.search("x") == []


def test_protocol_conformance(tmp_path: Path) -> None:
    assert isinstance(FileMemoryStore(tmp_path), MemoryStore)


def test_project_memory_dir_is_stable_and_distinct(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo_a = tmp_path / "alpha"
    repo_b = tmp_path / "beta"
    a1 = project_memory_dir(home, repo_a)
    a2 = project_memory_dir(home, repo_a)
    b = project_memory_dir(home, repo_b)
    assert a1 == a2  # stable across calls
    assert a1 != b  # distinct per project
    assert a1.parent == home / "memory"
    assert a1.name.startswith("alpha-")
