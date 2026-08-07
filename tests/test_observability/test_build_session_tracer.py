"""build_session_tracer respects the OTEL env gate."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from dream.observability._composite import CompositeTracer
from dream.observability._factory import build_session_tracer
from dream.observability._otel_provider import reset_otel_provider_for_tests
from dream.observability._tracer import JsonlTracer, NoopTracer
from dream.observability._writer import TraceWriter


def test_build_without_endpoint_is_jsonl(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    tracer = build_session_tracer(
        session_id="s1",
        task_id="t1",
        writer=TraceWriter(tmp_path / "trace.jsonl"),
    )
    assert isinstance(tracer, JsonlTracer)


def test_build_without_writer_is_noop(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    tracer = build_session_tracer(session_id="s1", task_id=None, writer=None)
    assert isinstance(tracer, NoopTracer)


def test_build_with_endpoint_composites(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    reset_otel_provider_for_tests()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    tracer = build_session_tracer(
        session_id="s1",
        task_id="t1",
        writer=TraceWriter(tmp_path / "trace.jsonl"),
    )
    assert isinstance(tracer, CompositeTracer)
    reset_otel_provider_for_tests()
