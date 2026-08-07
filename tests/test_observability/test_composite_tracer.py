"""CompositeTracer fans out to JSONL + OTel."""

from __future__ import annotations

from itertools import count

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dream.observability._composite import CompositeTracer
from dream.observability._events import TraceEvent
from dream.observability._otel_config import OtelConfig
from dream.observability._otel_provider import build_tracer_provider, reset_otel_provider_for_tests
from dream.observability._otel_tracer import OtelTracer
from dream.observability._tracer import JsonlTracer
from dream.utils.clock import FakeClock


class _RecordingWriter:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self.events.append(event)


def setup_function() -> None:
    reset_otel_provider_for_tests()


def teardown_function() -> None:
    reset_otel_provider_for_tests()


def test_composite_writes_jsonl_and_otel() -> None:
    writer = _RecordingWriter()
    ids = (f"span_{n}" for n in count(1))
    jsonl = JsonlTracer(
        writer,  # type: ignore[arg-type]
        session_id="s1",
        task_id="t1",
        clock=FakeClock(start_ms=1000),
        id_factory=lambda: next(ids),
    )
    memory = InMemorySpanExporter()
    handle = build_tracer_provider(
        OtelConfig(
            enabled=True,
            endpoint="http://127.0.0.1:4318",
            service_name="dream-test",
            service_version="0.0.0",
            insecure=True,
        ),
        span_exporter=memory,
    )
    otel = OtelTracer(handle.tracer, session_id="s1", task_id="t1")
    composite = CompositeTracer((jsonl, otel))
    with composite.span("llm.call", {"gen_ai.request.model": "m"}) as span:
        span.set("gen_ai.usage.prompt_tokens", 3)
        composite.event("tool.result", {"ok": True})
    handle.force_flush()
    assert len(writer.events) == 2  # llm.call + tool.result
    assert writer.events[0].event_type == "tool.result" or writer.events[1].event_type == "llm.call"
    assert any(e.event_type == "llm.call" for e in writer.events)
    assert any(s.name == "llm.call" for s in memory.get_finished_spans())
