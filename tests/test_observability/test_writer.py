"""Spec 12a — TraceWriter (append-only JSONL sink) + trace-log path."""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.observability._events import TraceEvent, from_jsonl_line
from dream.observability._writer import TraceWriter


def _event(span: str, event_type: str = "tool.result") -> TraceEvent:
    return TraceEvent(
        ts="2026-06-07T00:00:00.000Z",
        session_id="s1",
        task_id="T1",
        event_type=event_type,  # type: ignore[arg-type]
        span_id=span,
        parent_span_id=None,
        attributes={},
    )


def test_trace_log_path_under_task_sidecar(tmp_path: Path) -> None:
    paths = DreamPaths(repo=tmp_path, home=tmp_path / "home")
    expected = tmp_path / ".dream" / "sidecars" / "T1" / "logs" / "trace.jsonl"
    assert paths.trace_log("T1") == expected


def test_writer_appends_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "trace.jsonl"
    writer = TraceWriter(path)
    writer.write(_event("a"))
    writer.write(_event("b"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [from_jsonl_line(line).span_id for line in lines] == ["a", "b"]


def test_writer_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "trace.jsonl"
    TraceWriter(path).write(_event("a"))
    assert path.is_file()


def test_writer_holds_no_persistent_handle(tmp_path: Path) -> None:
    # Writing twice must not depend on (or leak) an open handle between calls.
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.write(_event("a"))
    writer.write(_event("b"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
