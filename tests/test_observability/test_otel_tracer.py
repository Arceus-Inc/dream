"""OTel tracer + provider — real spans via InMemorySpanExporter."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dream.observability._otel_config import OtelConfig
from dream.observability._otel_provider import build_tracer_provider, reset_otel_provider_for_tests
from dream.observability._otel_tracer import OtelTracer


def setup_function() -> None:
    reset_otel_provider_for_tests()


def teardown_function() -> None:
    reset_otel_provider_for_tests()


def _config() -> OtelConfig:
    return OtelConfig(
        enabled=True,
        endpoint="http://127.0.0.1:4318",
        service_name="dream-test",
        service_version="0.0.0",
    )


def test_nested_spans_exported() -> None:
    memory = InMemorySpanExporter()
    handle = build_tracer_provider(_config(), span_exporter=memory)
    tracer = OtelTracer(handle.tracer, session_id="s1", task_id="t1")
    with tracer.span("llm.call", {"gen_ai.request.model": "m"}):
        tracer.event("tool.result", {"tool.name": "bash", "tool.is_error": False})
    handle.force_flush()
    spans = memory.get_finished_spans()
    names = [s.name for s in spans]
    assert "tool.result" in names
    assert "llm.call" in names
    llm = next(s for s in spans if s.name == "llm.call")
    assert llm.attributes is not None
    assert llm.attributes.get("dream.session_id") == "s1"
    assert llm.attributes.get("gen_ai.request.model") == "m"


def test_error_marks_span_status() -> None:
    memory = InMemorySpanExporter()
    handle = build_tracer_provider(_config(), span_exporter=memory)
    tracer = OtelTracer(handle.tracer, session_id="s1", task_id=None)
    try:
        with tracer.span("tool.call", {"tool.name": "x"}):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    handle.force_flush()
    spans = memory.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code.name == "ERROR"
