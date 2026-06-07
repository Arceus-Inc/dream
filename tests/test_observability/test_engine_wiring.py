"""Spec 12a — engine emits trace events at the live chokepoints.

run_query → llm.call (span, with usage) + tool.call/tool.result (nested under it).
run_session → state.transition (via the TransitionBus) + validator.finding (from
the orientation brief). NoopTracer is the default, so untraced runs are unchanged.
"""

from __future__ import annotations

from itertools import count

from dream.engine._cost import UsageSnapshot
from dream.engine._loop import QueryContext, run_query
from dream.engine._messages import ConversationMessage, TextBlock, ToolUseBlock
from dream.engine._orientation import OrientationBrief, OrientationConfig, ValidatorFinding
from dream.engine._session import SessionConfig, run_session
from dream.engine._transitions import TransitionBus
from dream.observability._events import TraceEvent
from dream.observability._tracer import JsonlTracer
from dream.utils.clock import FakeClock
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


class _RecordingWriter:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self.events.append(event)


def _tracer(writer: _RecordingWriter) -> JsonlTracer:
    ids = (f"span_{n}" for n in count(1))
    return JsonlTracer(
        writer,  # type: ignore[arg-type]
        session_id="s1",
        task_id="T1",
        clock=FakeClock(start_ms=1000),
        id_factory=lambda: next(ids),
    )


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


async def test_run_query_emits_llm_tool_events_nested() -> None:
    writer = _RecordingWriter()
    tu = ToolUseBlock(id="u1", name="bash", input={"cmd": "ls"})
    streamer = FakeStreamer(
        [
            FakeTurn(tool_uses=[tu], usage=UsageSnapshot(input_tokens=10, output_tokens=5)),
            FakeTurn(text_chunks=["done"]),  # no tools → loop ends
        ]
    )
    ctx = QueryContext(
        client=streamer,
        tools=FakeDispatcher(),
        max_turns=8,
        tracer=_tracer(writer),
        model="gpt-4o",
    )
    async for _ in run_query(ctx, [_user("hi")]):
        pass

    llm = [e for e in writer.events if e.event_type == "llm.call"]
    tcall = [e for e in writer.events if e.event_type == "tool.call"]
    tres = [e for e in writer.events if e.event_type == "tool.result"]
    assert len(llm) == 2  # two turns
    assert len(tcall) == 1 and len(tres) == 1
    # First turn's llm.call is the span the tool events nest under.
    first_llm = next(e for e in llm if e.span_id == "span_1")
    assert tcall[0].parent_span_id == first_llm.span_id
    assert tres[0].parent_span_id == first_llm.span_id
    assert first_llm.attributes["gen_ai.usage.prompt_tokens"] == 10
    assert first_llm.attributes["gen_ai.request.model"] == "gpt-4o"
    assert tcall[0].attributes["tool.name"] == "bash"
    assert tres[0].attributes["tool.is_error"] is False


def test_query_context_defaults_to_noop_tracer() -> None:
    ctx = QueryContext(client=FakeStreamer([]), tools=FakeDispatcher())
    # NoopTracer.span/event must be usable without a writer.
    with ctx.tracer.span("llm.call"):
        ctx.tracer.event("tool.result", {})


async def test_run_session_emits_transitions_and_findings() -> None:
    writer = _RecordingWriter()

    async def gather() -> OrientationBrief:
        return OrientationBrief(
            repo_summary="r",
            progress_tail="p",
            active_exec_plan="a",
            validator_findings=(ValidatorFinding("warning", "V-1", "heads up"),),
        )

    config = SessionConfig(
        client=FakeStreamer([FakeTurn(text_chunks=["hi"])]),
        tools=FakeDispatcher(),
        session_id="s1",
        orientation=OrientationConfig(gather=gather),
        tracer=_tracer(writer),
        model="gpt-4o-test",
    )
    bus = TransitionBus()
    async for _ in run_session(config, [_user("go")], transitions=bus):
        pass

    transitions = [e for e in writer.events if e.event_type == "state.transition"]
    findings = [e for e in writer.events if e.event_type == "validator.finding"]
    llm = [e for e in writer.events if e.event_type == "llm.call"]
    assert transitions, "expected at least one state.transition event"
    assert llm and all(e.attributes["gen_ai.request.model"] == "gpt-4o-test" for e in llm)
    assert any(
        e.attributes["transition.from"] == "starting"
        and e.attributes["transition.to"] == "orienting"
        for e in transitions
    )
    assert len(findings) == 1
    assert findings[0].attributes["finding.code"] == "V-1"
    assert findings[0].attributes["finding.severity"] == "warning"
