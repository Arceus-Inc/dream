"""``session_search`` — search-only episodic recall (no get_run drill-down)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dream.contracts.episodic import EpisodicRecord, EpisodicSearchHit
from dream.memory import EpisodicContext, put_episodic_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.session_search import SessionSearchTool


class _FakeEpisodicStore:
    """Protocol-compliant fake — test-local only, not shipped in dream.memory."""

    def __init__(self, records: Sequence[EpisodicRecord]) -> None:
        self._records = list(records)

    async def search(self, query: str, *, limit: int = 5) -> Sequence[EpisodicSearchHit]:
        terms = [t for t in query.lower().split() if t]
        hits: list[EpisodicSearchHit] = []
        for record in self._records:
            hay = f"{record.intent}\n{record.body}".lower()
            if terms and all(t in hay for t in terms):
                hits.append(EpisodicSearchHit(record=record, snippet=record.intent))
            if len(hits) >= limit:
                break
        return hits


def _record(run_id: str, intent: str, body: str = "", outcome: str = "ok") -> EpisodicRecord:
    return EpisodicRecord(
        run_id=run_id,
        intent=intent,
        outcome=outcome,
        body=body or intent,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        task_id="t1",
        files_touched=("a.py",),
    )


def _ctx(working_dir: Path, store: _FakeEpisodicStore | None) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if store is not None:
        put_episodic_context(metadata, EpisodicContext(store=store))
    return ToolExecutionContext(
        working_dir=working_dir, session_id="s_ep", metadata=metadata
    )


def test_session_search_is_read_only_tier_0() -> None:
    tool = SessionSearchTool()
    assert tool.name == "session_search"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


async def test_session_search_returns_slim_hits(tmp_path: Path) -> None:
    store = _FakeEpisodicStore(
        [
            _record("r1", "auth refactor", "rewrote login middleware"),
            _record("r2", "billing cron", "fixed invoice timezone"),
        ]
    )
    result = await SessionSearchTool().execute(
        {"query": "auth middleware"}, _ctx(tmp_path, store)
    )
    assert result.is_error is False
    assert "r1" in result.content
    assert "auth refactor" in result.content
    assert result.metadata.get("hit_count") == 1
    assert result.metadata.get("run_ids") == ["r1"]


async def test_session_search_no_matches(tmp_path: Path) -> None:
    store = _FakeEpisodicStore([_record("r1", "auth refactor")])
    result = await SessionSearchTool().execute(
        {"query": "zzzz-nothing"}, _ctx(tmp_path, store)
    )
    assert result.is_error is False
    assert result.metadata.get("hit_count") == 0


async def test_session_search_no_store_is_graceful(tmp_path: Path) -> None:
    result = await SessionSearchTool().execute(
        {"query": "anything"}, _ctx(tmp_path, None)
    )
    assert result.is_error is False
    assert "not available" in result.content.lower()
    assert result.metadata.get("hit_count") == 0


async def test_session_search_empty_query_is_error(tmp_path: Path) -> None:
    store = _FakeEpisodicStore([_record("r1", "auth")])
    result = await SessionSearchTool().execute({"query": "  "}, _ctx(tmp_path, store))
    assert result.is_error is True
    assert "empty" in result.metadata["root_cause"].lower()


async def test_session_search_has_no_run_id_input() -> None:
    """Search-only: schema must not expose get_run-style drill-down."""
    schema = SessionSearchTool().input_schema()
    props = schema.get("properties") or {}
    assert "query" in props
    assert "run_id" not in props
    assert "session_id" not in props
