"""Unit tests for dream.memory._catalogue — memory catalogue rendering.

Covers render_memory_catalogue(), memory_description(), and the private
_first_nonempty_line() helper.
"""

from __future__ import annotations

from dream.contracts.memory import MemoryRecord, MemoryScope, MemoryType
from dream.memory._catalogue import (
    _DESCRIPTION_MAX,
    memory_description,
    render_memory_catalogue,
)


def _record(
    id: str,
    content: str = "body",
    description: str | None = None,
    **kwargs: object,
) -> MemoryRecord:
    frontmatter: dict[str, object] = {}
    if description is not None:
        frontmatter["description"] = description
    return MemoryRecord(
        id=id,
        content=content,
        frontmatter=frontmatter,
        type=MemoryType.USER,
        scope=MemoryScope.PROJECT,
    )


# --- render_memory_catalogue ---


def test_empty_records_returns_empty_string() -> None:
    assert render_memory_catalogue([]) == ""


def test_single_record_renders_header_and_entry() -> None:
    result = render_memory_catalogue([_record("note-1", description="my note")])
    assert "# Workspace memory" in result
    assert "note-1" in result
    assert "my note" in result


def test_records_sorted_by_id() -> None:
    records = [
        _record("z-last", description="last"),
        _record("a-first", description="first"),
    ]
    result = render_memory_catalogue(records)
    lines = result.splitlines()
    entries = [line for line in lines if line.startswith("- ")]
    assert entries[0].startswith("- a-first")
    assert entries[1].startswith("- z-last")


def test_multiple_records_all_listed() -> None:
    records = [_record(f"rec-{i}", description=f"desc {i}") for i in range(5)]
    result = render_memory_catalogue(records)
    for i in range(5):
        assert f"rec-{i}" in result
        assert f"desc {i}" in result


# --- memory_description ---


def test_description_from_frontmatter() -> None:
    rec = _record("x", description="explicit description")
    assert memory_description(rec) == "explicit description"


def test_description_falls_back_to_body_first_line() -> None:
    rec = _record("x", content="first line\nsecond line")
    assert memory_description(rec) == "first line"


def test_description_skips_empty_body_lines() -> None:
    rec = _record("x", content="\n\n  \nactual content\nmore")
    assert memory_description(rec) == "actual content"


def test_description_truncated_when_too_long() -> None:
    long_desc = "a" * 200
    rec = _record("x", description=long_desc)
    desc = memory_description(rec)
    assert len(desc) <= _DESCRIPTION_MAX
    assert desc.endswith("…")


def test_description_not_truncated_at_boundary() -> None:
    exact = "a" * _DESCRIPTION_MAX
    rec = _record("x", description=exact)
    assert memory_description(rec) == exact


def test_description_empty_body_returns_empty_string() -> None:
    rec = _record("x", content="")
    assert memory_description(rec) == ""


def test_description_whitespace_only_body() -> None:
    rec = _record("x", content="   \n  \n   ")
    assert memory_description(rec) == ""


def test_description_frontmatter_non_string() -> None:
    rec = _record("x", content="fallback")
    rec.frontmatter["description"] = 42
    desc = memory_description(rec)
    assert desc == "42"
