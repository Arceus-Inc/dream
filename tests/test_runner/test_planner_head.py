"""Spec 10 slice G3 — production planner head (structured-output P2).

``make_planner_head(harness, ...)`` returns a :data:`PlannerCallable`
that opens a ``planner``-bound session through ``Harness.run_role`` with
native ``response_format`` and parses a JSON :class:`PlannerResponse`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from dream.api.response_format import ResponseFormatKind
from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StreamEvent,
)
from dream.engine._messages import ConversationMessage, TextBlock
from dream.harness import Harness, HarnessConfig
from dream.planner import LedgerStep, PlannerOutput
from dream.runner import (
    PlannerHeadParseError,
    RoleSessionError,
    make_planner_head,
)
from dream.runner._planner_schema import PlannerLedgerBody, PlannerResponse, PlannerStepBody
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher


class _ScriptedReplyStreamer:
    """Returns a single canned assistant turn; records the user prompts seen."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        yield AssistantTextDelta(text=self._reply)
        yield AssistantTurnComplete(
            blocks=[TextBlock(text=self._reply)],
            usage=UsageSnapshot(),
        )

    @property
    def last_user_text(self) -> str:
        last = self.calls[-1]
        user_msgs = [m for m in last if m.role == "user"]
        assert user_msgs, "no user message in last call"
        return _flatten(user_msgs[-1])


def _flatten(message: ConversationMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts)


def _harness_with_reply(reply: str) -> tuple[Harness, _ScriptedReplyStreamer]:
    streamer = _ScriptedReplyStreamer(reply)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    return Harness(config), streamer


def _harness_capturing_options(
    reply: str,
) -> tuple[Harness, _ScriptedReplyStreamer, list[SessionOptions]]:
    captured: list[SessionOptions] = []
    streamer = _ScriptedReplyStreamer(reply)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        captured.append(options)
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    return Harness(config), streamer, captured


def _valid_reply(
    *,
    spec: str = "# Plan\n\nDo the thing.",
    steps: list[PlannerStepBody] | None = None,
    evaluator_enabled: bool | None = None,
) -> str:
    if steps is None:
        steps = [PlannerStepBody(id="s1", description="do thing one", sprint_target=None, notes="")]
    enabled = True if evaluator_enabled is None else evaluator_enabled
    return PlannerResponse(
        spec_markdown=spec,
        ledger=PlannerLedgerBody(steps=steps, evaluator_enabled=enabled),
    ).model_dump_json()


def test_make_planner_head_returns_callable() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)
    assert callable(head)


@pytest.mark.asyncio
async def test_planner_head_parses_valid_json_reply() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert isinstance(out, PlannerOutput)
    assert out.spec_markdown == "# Plan\n\nDo the thing."
    assert out.ledger.task_id == "task-001"
    assert out.ledger.intent == "ship it"
    assert out.ledger.steps == (LedgerStep(id="s1", description="do thing one"),)
    assert out.ledger.evaluator_enabled is True


@pytest.mark.asyncio
async def test_planner_head_attaches_response_format() -> None:
    harness, _, captured = _harness_capturing_options(_valid_reply())
    head = make_planner_head(harness)

    await head("task-001", "ship it")

    assert captured
    rf = captured[0].response_format
    assert rf is not None
    assert rf.kind is ResponseFormatKind.JSON_SCHEMA
    assert rf.json_schema is not None
    assert rf.json_schema.name == "planner_response"
    assert rf.json_schema.strict is True


@pytest.mark.asyncio
async def test_planner_head_intent_includes_task_id_and_user_intent() -> None:
    harness, streamer = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    await head("task-xyz", "build a robot")

    prompt = streamer.last_user_text
    assert "task-xyz" in prompt
    assert "build a robot" in prompt


@pytest.mark.asyncio
async def test_planner_head_intent_documents_json_contract() -> None:
    harness, streamer = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    await head("task-001", "ship it")

    prompt = streamer.last_user_text
    assert "spec_markdown" in prompt
    assert "JSON object" in prompt
    assert "<spec>" not in prompt


@pytest.mark.asyncio
async def test_planner_head_strips_json_code_fence() -> None:
    body = _valid_reply(
        steps=[PlannerStepBody(id="s1", description="first", sprint_target=None, notes="")]
    )
    harness, _ = _harness_with_reply(f"```json\n{body}\n```")
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.steps == (LedgerStep(id="s1", description="first"),)


@pytest.mark.asyncio
async def test_planner_head_raises_when_spec_empty() -> None:
    bad = (
        '{"spec_markdown":"","ledger":{"steps":[{"id":"s1","description":"x"}],'
        '"evaluator_enabled":true}}'
    )
    harness, _ = _harness_with_reply(bad)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="schema"):
        await head("task-001", "ship it")


@pytest.mark.asyncio
async def test_planner_head_raises_when_steps_empty() -> None:
    bad = '{"spec_markdown":"# Plan","ledger":{"steps":[],"evaluator_enabled":true}}'
    harness, _ = _harness_with_reply(bad)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="schema"):
        await head("task-001", "ship it")


@pytest.mark.asyncio
async def test_planner_head_raises_on_invalid_json() -> None:
    harness, _ = _harness_with_reply("{not json}")
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError):
        await head("task-001", "ship it")


@pytest.mark.asyncio
async def test_planner_head_raises_when_step_missing_id() -> None:
    bad = (
        '{"spec_markdown":"# Plan","ledger":{"steps":[{"description":"no id"}],'
        '"evaluator_enabled":true}}'
    )
    harness, _ = _harness_with_reply(bad)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="schema"):
        await head("task-001", "ship it")


@pytest.mark.asyncio
async def test_planner_head_evaluator_enabled_false_round_trips() -> None:
    harness, _ = _harness_with_reply(_valid_reply(evaluator_enabled=False))
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.evaluator_enabled is False


@pytest.mark.asyncio
async def test_planner_head_propagates_role_session_error() -> None:
    class _BoomStreamer(_ScriptedReplyStreamer):
        async def stream_turn(
            self, messages: Sequence[ConversationMessage]
        ) -> AsyncIterator[StreamEvent]:
            yield ErrorEvent(message="boom", recoverable=False)

    streamer = _BoomStreamer(_valid_reply())

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    head = make_planner_head(harness)

    with pytest.raises(RoleSessionError):
        await head("task-001", "ship it")
