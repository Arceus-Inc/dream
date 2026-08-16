"""CompositeTracer fans out to JSONL + OTel and only exits entered spans."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from itertools import count

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dream.observability._composite import CompositeTracer
from dream.observability._events import TraceEvent, TraceEventType
from dream.observability._otel_config import OtelConfig
from dream.observability._otel_provider import (
    OtelProviderHandle,
    build_tracer_provider,
    reset_otel_provider_for_tests,
)
from dream.observability._otel_tracer import OtelTracer
from dream.observability._tracer import JsonlTracer, Span
from dream.utils.clock import FakeClock


class _RecordingWriter:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self.events.append(event)


class _RecordingChild:
    """Minimal Tracer that records enter/exit without OTel."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.exit_exc: BaseException | None = None

    @contextmanager
    def span(
        self,
        event_type: TraceEventType,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        del event_type, attributes
        self.entered = True
        try:
            yield Span("jsonl-primary")
        except BaseException as exc:
            self.exit_exc = exc
            self.exited = True
            raise
        else:
            self.exited = True

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        del event_type, attributes


class _CaptureSibling:
    """Sibling tracer that retains the live span handle for extra assertions."""

    def __init__(self) -> None:
        self.handle: Span | None = None
        self.events: list[tuple[TraceEventType, dict[str, object]]] = []

    @contextmanager
    def span(
        self,
        event_type: TraceEventType,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        del event_type, attributes
        self.handle = Span("otel-sibling")
        yield self.handle

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        self.events.append((event_type, dict(attributes)))


class _EnterBoom:
    """Tracer whose span manager raises before enter completes."""

    def __init__(self) -> None:
        self.exited = False

    def span(
        self,
        event_type: TraceEventType,
        attributes: Mapping[str, object] | None = None,
    ) -> _BoomManager:
        del event_type, attributes
        return _BoomManager(self)

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        del event_type, attributes


class _BoomManager:
    def __init__(self, owner: _EnterBoom) -> None:
        self._owner = owner

    def __enter__(self) -> Span:
        raise RuntimeError("otel enter failed")

    def __exit__(self, *args: object) -> None:
        self._owner.exited = True
        raise AssertionError("must not exit unentered manager")


class _EventBoom:
    """Sibling whose event() fails; span lifecycle is a no-op handle."""

    @contextmanager
    def span(
        self,
        event_type: TraceEventType,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        del event_type, attributes
        yield Span("event-boom")

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        del event_type, attributes
        raise RuntimeError("otel event failed")


def setup_function() -> None:
    reset_otel_provider_for_tests()


def teardown_function() -> None:
    reset_otel_provider_for_tests()


def _jsonl_tracer(writer: _RecordingWriter) -> JsonlTracer:
    ids = (f"span_{n}" for n in count(1))
    return JsonlTracer(
        writer,  # type: ignore[arg-type]
        session_id="s1",
        task_id="t1",
        clock=FakeClock(start_ms=1000),
        id_factory=lambda: next(ids),
    )


def _otel_pair() -> tuple[OtelTracer, InMemorySpanExporter, OtelProviderHandle]:
    memory = InMemorySpanExporter()
    handle = build_tracer_provider(
        OtelConfig(
            enabled=True,
            endpoint="http://127.0.0.1:4318",
            service_name="dream-test",
            service_version="0.0.0",
        ),
        span_exporter=memory,
    )
    otel = OtelTracer(handle.tracer, session_id="s1", task_id="t1")
    return otel, memory, handle


def _finished_llm(memory: InMemorySpanExporter) -> object:
    llm = next(s for s in memory.get_finished_spans() if s.name == "llm.call")
    return llm


def test_composite_writes_jsonl_and_otel() -> None:
    writer = _RecordingWriter()
    otel, memory, handle = _otel_pair()
    composite = CompositeTracer((_jsonl_tracer(writer), otel))
    with composite.span("llm.call", {"gen_ai.request.model": "m"}) as span:
        span.set("gen_ai.usage.prompt_tokens", 3)
        span.update({"gen_ai.usage.completion_tokens": 1})
        composite.event("tool.result", {"ok": True})
    handle.force_flush()
    assert len(writer.events) == 2  # llm.call + tool.result
    assert any(e.event_type == "llm.call" for e in writer.events)
    llm = _finished_llm(memory)
    assert llm.attributes is not None
    assert llm.attributes.get("gen_ai.usage.prompt_tokens") == 3
    assert llm.attributes.get("gen_ai.usage.completion_tokens") == 1


def test_composite_propagates_extras_to_otel_on_exception() -> None:
    writer = _RecordingWriter()
    otel, memory, handle = _otel_pair()
    composite = CompositeTracer((_jsonl_tracer(writer), otel))
    with pytest.raises(RuntimeError, match="boom"):
        with composite.span("llm.call") as span:
            span.set("gen_ai.usage.prompt_tokens", 3)
            span.update({"gen_ai.usage.completion_tokens": 1})
            raise RuntimeError("boom")
    handle.force_flush()
    jsonl = next(e for e in writer.events if e.event_type == "llm.call")
    assert jsonl.attributes["gen_ai.usage.prompt_tokens"] == 3
    llm = _finished_llm(memory)
    assert llm.attributes is not None
    assert llm.attributes.get("gen_ai.usage.prompt_tokens") == 3
    assert llm.attributes.get("gen_ai.usage.completion_tokens") == 1
    assert llm.status.status_code.name == "ERROR"


def test_composite_propagates_extras_on_cancellation() -> None:
    sibling = _CaptureSibling()
    composite = CompositeTracer((_RecordingChild(), sibling))  # type: ignore[arg-type]
    with pytest.raises(KeyboardInterrupt):
        with composite.span("llm.call") as span:
            span.set("gen_ai.usage.prompt_tokens", 7)
            raise KeyboardInterrupt
    assert sibling.handle is not None
    assert sibling.handle.end_attributes()["gen_ai.usage.prompt_tokens"] == 7


def test_composite_propagates_extras_on_generator_close() -> None:
    sibling = _CaptureSibling()
    composite = CompositeTracer((_RecordingChild(), sibling))  # type: ignore[arg-type]
    cm = composite.span("llm.call")
    span = cm.__enter__()
    span.set("gen_ai.usage.prompt_tokens", 9)
    cm.__exit__(GeneratorExit, GeneratorExit(), None)
    assert sibling.handle is not None
    assert sibling.handle.end_attributes()["gen_ai.usage.prompt_tokens"] == 9


def test_sibling_event_failure_does_not_abort_after_jsonl() -> None:
    writer = _RecordingWriter()
    composite = CompositeTracer((_jsonl_tracer(writer), _EventBoom()))  # type: ignore[arg-type]
    with composite.span("llm.call"):
        composite.event("tool.result", {"ok": True})
    assert any(e.event_type == "tool.result" for e in writer.events)
    assert any(e.event_type == "llm.call" for e in writer.events)


def test_composite_exits_only_entered_managers_on_enter_failure() -> None:
    jsonl = _RecordingChild()
    boom = _EnterBoom()
    composite = CompositeTracer((jsonl, boom))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="otel enter failed"):
        with composite.span("llm.call"):
            raise AssertionError("must not yield after a child failed to enter")
    assert jsonl.entered is True
    assert jsonl.exited is True
    assert isinstance(jsonl.exit_exc, RuntimeError)
    assert boom.exited is False


def test_composite_closes_jsonl_span_when_otel_enter_fails() -> None:
    writer = _RecordingWriter()
    boom = _EnterBoom()
    composite = CompositeTracer((_jsonl_tracer(writer), boom))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="otel enter failed"):
        with composite.span("llm.call", {"a": 1}):
            pass
    assert len(writer.events) == 1
    assert writer.events[0].event_type == "llm.call"
    assert boom.exited is False
