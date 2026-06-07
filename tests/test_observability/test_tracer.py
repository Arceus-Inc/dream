"""Spec 12a — JsonlTracer span nesting + NoopTracer + async contextvar isolation."""

from __future__ import annotations

import asyncio
from itertools import count

from dream.observability._events import TraceEvent
from dream.observability._tracer import JsonlTracer, NoopTracer
from dream.utils.clock import FakeClock


class _RecordingWriter:
    """Captures written TraceEvents (structurally a TraceWriter)."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self.events.append(event)


def _tracer(writer: _RecordingWriter, clock: FakeClock | None = None) -> JsonlTracer:
    ids = (f"span_{n}" for n in count(1))
    return JsonlTracer(
        writer,  # type: ignore[arg-type]
        session_id="s1",
        task_id="T1",
        clock=clock or FakeClock(start_ms=1000),
        id_factory=lambda: next(ids),
    )


def test_noop_tracer_emits_nothing() -> None:
    tracer = NoopTracer()
    with tracer.span("llm.call", {"x": 1}):
        tracer.event("tool.result", {"y": 2})
    # No writer, no error, nothing recorded — purely a no-op.


def test_span_emits_one_line_at_close() -> None:
    writer = _RecordingWriter()
    with _tracer(writer).span("llm.call", {"a": 1}):
        pass
    assert len(writer.events) == 1
    ev = writer.events[0]
    assert ev.event_type == "llm.call"
    assert ev.parent_span_id is None
    assert ev.attributes["a"] == 1


def test_nested_spans_link_parent_chain() -> None:
    writer = _RecordingWriter()
    tracer = _tracer(writer)
    with tracer.span("llm.call"):  # span_1
        with tracer.span("tool.call"):  # span_2
            with tracer.span("llm.call"):  # span_3
                pass
    # Emitted at close → innermost first: span_3, span_2, span_1.
    by_id = {e.span_id: e for e in writer.events}
    a, b, c = "span_1", "span_2", "span_3"
    assert by_id[c].parent_span_id == b
    assert by_id[b].parent_span_id == a
    assert by_id[a].parent_span_id is None


def test_point_event_parents_to_current_span() -> None:
    writer = _RecordingWriter()
    tracer = _tracer(writer)
    with tracer.span("tool.call"):  # span_1
        tracer.event("tool.result", {"ok": True})  # span_2, parent span_1
    result = next(e for e in writer.events if e.event_type == "tool.result")
    assert result.parent_span_id == "span_1"


def test_span_handle_enriches_end_attributes() -> None:
    writer = _RecordingWriter()
    with _tracer(writer).span("llm.call") as span:
        span.set("gen_ai.usage.prompt_tokens", 10)
    assert writer.events[0].attributes["gen_ai.usage.prompt_tokens"] == 10


def test_span_records_duration_from_clock() -> None:
    writer = _RecordingWriter()
    clock = FakeClock(start_ms=1000)
    with _tracer(writer, clock).span("llm.call"):
        clock.advance(250)
    assert writer.events[0].attributes["duration_ms"] == 250


def test_event_carries_session_and_task() -> None:
    writer = _RecordingWriter()
    _tracer(writer).event("validator.finding", {"finding.code": "x"})
    ev = writer.events[0]
    assert ev.session_id == "s1" and ev.task_id == "T1"


async def test_concurrent_tasks_do_not_interleave_spans() -> None:
    writer = _RecordingWriter()
    tracer = _tracer(writer)

    async def task(label: str) -> None:
        with tracer.span("llm.call"):
            await asyncio.sleep(0)
            tracer.event("tool.result", {"label": label})

    await asyncio.gather(task("a"), task("b"))

    # Each tool.result must parent to a *span* (its own task's llm.call), and the
    # two results must have different parents — no cross-task interleaving.
    results = [e for e in writer.events if e.event_type == "tool.result"]
    span_ids = {e.span_id for e in writer.events if e.event_type == "llm.call"}
    assert len(results) == 2
    assert all(r.parent_span_id in span_ids for r in results)
    assert results[0].parent_span_id != results[1].parent_span_id


async def test_span_in_async_generator_survives_per_anext_task_driver() -> None:
    """Regression: ``with tracer.span(...)`` inside an async generator must not
    crash when the consumer drives ``__anext__`` from a fresh ``Task`` each
    time (which is exactly what ``Session.send`` does for cancel routing).

    The first ``set(token)`` runs in one Context; the matching ``reset(token)``
    on ``with`` exit runs in a *different* Context, and ``ContextVar.reset``
    raises ``ValueError`` cross-Context. Falling back to ``set(parent)`` keeps
    nesting correct without crashing the act-loop.
    """
    writer = _RecordingWriter()
    tracer = _tracer(writer)

    async def gen():
        # Open a span, yield mid-body so the consumer can drive __anext__ in
        # a different task/Context, then close the span on the next resume.
        with tracer.span("llm.call"):
            yield "open"
            yield "still-open"

    g = gen()
    aiter_ = g.__aiter__()
    while True:
        # Mimic Session.send: one fresh Task per __anext__ call.
        try:
            await asyncio.ensure_future(aiter_.__anext__())
        except StopAsyncIteration:
            break
    await g.aclose()

    # Exactly one span event recorded (close-on-exit), and no exception.
    spans = [e for e in writer.events if e.event_type == "llm.call"]
    assert len(spans) == 1
