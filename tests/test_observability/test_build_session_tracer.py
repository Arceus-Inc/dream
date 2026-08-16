"""build_session_tracer — OTel on by default; explicit disable is JSONL-only."""

from __future__ import annotations

import warnings
from pathlib import Path

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pytest import MonkeyPatch

from dream.observability import _factory
from dream.observability._composite import CompositeTracer
from dream.observability._factory import build_session_tracer
from dream.observability._otel_provider import reset_otel_provider_for_tests
from dream.observability._tracer import JsonlTracer, NoopTracer
from dream.observability._writer import TraceWriter


def test_build_default_composites(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    reset_otel_provider_for_tests()
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(
        "dream.observability._otel_provider._build_otlp_exporter",
        lambda endpoint: InMemorySpanExporter(),
    )
    tracer = build_session_tracer(
        session_id="s1",
        task_id="t1",
        writer=TraceWriter(tmp_path / "trace.jsonl"),
    )
    assert isinstance(tracer, CompositeTracer)
    reset_otel_provider_for_tests()


def test_build_disabled_is_jsonl_only(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    reset_otel_provider_for_tests()
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    tracer = build_session_tracer(
        session_id="s1",
        task_id="t1",
        writer=TraceWriter(tmp_path / "trace.jsonl"),
    )
    assert isinstance(tracer, JsonlTracer)
    reset_otel_provider_for_tests()


def test_build_without_writer_is_noop_when_disabled(monkeypatch: MonkeyPatch) -> None:
    reset_otel_provider_for_tests()
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    tracer = build_session_tracer(session_id="s1", task_id=None, writer=None)
    assert isinstance(tracer, NoopTracer)
    reset_otel_provider_for_tests()


def test_missing_otel_warning_is_emitted_once(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(_factory, "is_otel_enabled", lambda: True)
    monkeypatch.setattr(_factory, "get_otel_tracer", lambda **_: None)
    _factory._missing_otel_warning_emitted = False

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for session_id in ("s1", "s2"):
            build_session_tracer(
                session_id=session_id,
                task_id=None,
                writer=TraceWriter(tmp_path / f"{session_id}.jsonl"),
            )

    assert len(caught) == 1
    assert "continuing with JSONL-only tracing" in str(caught[0].message)


def test_harness_session_uses_jsonl_when_disabled(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from dream import SessionOptions, build_harness

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    working = tmp_path / "wt"
    working.mkdir()
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=working,
        env={"DREAM_HOME": str(tmp_path / "home")},
        mcp=False,
        plugins=False,
        skills=False,
        memory=False,
    )
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]
    assert isinstance(engine.tracer, JsonlTracer)


def test_harness_session_uses_composite_when_enabled(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from dream import SessionOptions, build_harness

    reset_otel_provider_for_tests()
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(
        "dream.observability._otel_provider._build_otlp_exporter",
        lambda endpoint: InMemorySpanExporter(),
    )
    working = tmp_path / "wt"
    working.mkdir()
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=working,
        env={"DREAM_HOME": str(tmp_path / "home")},
        mcp=False,
        plugins=False,
        skills=False,
        memory=False,
    )
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]
    assert isinstance(engine.tracer, CompositeTracer)
    reset_otel_provider_for_tests()
