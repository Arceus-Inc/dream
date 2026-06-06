"""Spec 06.5 slice 1 — ``run_background_turn`` one-turn wake orchestrator.

Drives a single model turn whose only purpose is to emit a ``heartbeat``
tool call, captures that call structurally (the tool is "virtual" — the
runner never dispatches it), and returns a ``HeartbeatDecision``. There
is no orientation, no reviewer, no compaction, no liveness coma monitor:
this is deliberately separate from Spec 03's ``run_session`` because the
wake turn needs none of that machinery.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
)
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from dream.wake import HeartbeatDecision, run_background_turn

# --- minimal fake streamer (local; not shared with engine fakes) ------------


@dataclass
class _ScriptedTurn:
    text_chunks: list[str] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)


class _ScriptedStreamer:
    """Yields one scripted turn per ``stream_turn`` call.

    Captures the messages it was called with so tests can assert what the
    runner sent into the model (notably the wake stimulus / system prompt).
    """

    def __init__(self, turn: _ScriptedTurn) -> None:
        self._turn = turn
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        for chunk in self._turn.text_chunks:
            yield AssistantTextDelta(text=chunk)
        blocks: list[ContentBlock] = []
        joined = "".join(self._turn.text_chunks)
        if joined:
            blocks.append(TextBlock(text=joined))
        blocks.extend(self._turn.tool_uses)
        yield AssistantTurnComplete(blocks=blocks, usage=self._turn.usage)


def _now() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


# --- happy path -------------------------------------------------------------


async def test_run_returns_decision_from_tool_call() -> None:
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={
                        "action": "run",
                        "tasks": ["finish slice 1", "open PR"],
                        "reason": "exec plan ready",
                    },
                )
            ]
        )
    )
    decision = await run_background_turn(
        streamer, wake_source="idle_timer", now=_now
    )
    assert decision == HeartbeatDecision(
        decided_at=_now(),
        action="run",
        tasks=("finish slice 1", "open PR"),
        reason="exec plan ready",
        wake_source="idle_timer",
        forced=False,
        outcome="decided",
    )


async def test_run_skip_zeroes_tasks() -> None:
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={
                        "action": "skip",
                        "tasks": ["ignored"],
                        "reason": "nothing pending",
                    },
                )
            ]
        )
    )
    decision = await run_background_turn(
        streamer, wake_source="cron", now=_now
    )
    assert decision.action == "skip"
    assert decision.tasks == ()
    assert decision.reason == "nothing pending"
    assert decision.outcome == "decided"


# --- missing-decision outcomes ---------------------------------------------


async def test_no_tool_call_yields_missing_outcome() -> None:
    """Model produced prose only — counts as ``outcome="missing"``.

    Slice 1 records this as a skip with reason ``heartbeat_missing_decision``;
    slice 2's skip-streak counter will NOT advance on a missing outcome
    (the counter only counts honest skips).
    """
    streamer = _ScriptedStreamer(
        _ScriptedTurn(text_chunks=["I think I'll wait a bit."])
    )
    decision = await run_background_turn(
        streamer, wake_source="idle_timer", now=_now
    )
    assert decision.action == "skip"
    assert decision.outcome == "missing"
    assert decision.reason == "heartbeat_missing_decision"
    assert decision.forced is False


async def test_wrong_tool_name_yields_missing_outcome() -> None:
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="bash",
                    input={"command": "ls"},
                )
            ]
        )
    )
    decision = await run_background_turn(
        streamer, wake_source="idle_timer", now=_now
    )
    assert decision.action == "skip"
    assert decision.outcome == "missing"


async def test_schema_invalid_args_yields_missing_outcome() -> None:
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "wait", "reason": "bad enum"},
                )
            ]
        )
    )
    decision = await run_background_turn(
        streamer, wake_source="idle_timer", now=_now
    )
    assert decision.action == "skip"
    assert decision.outcome == "missing"


async def test_only_first_heartbeat_call_is_used() -> None:
    """Two ``heartbeat`` calls in one turn: take the first, ignore the rest."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "run", "tasks": ["a"], "reason": "first"},
                ),
                ToolUseBlock(
                    id="tu_2",
                    name="heartbeat",
                    input={"action": "skip", "tasks": [], "reason": "second"},
                ),
            ]
        )
    )
    decision = await run_background_turn(
        streamer, wake_source="cron", now=_now
    )
    assert decision.action == "run"
    assert decision.reason == "first"


# --- prompt wiring ----------------------------------------------------------


async def test_runner_threads_system_prompt_into_stimulus() -> None:
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "skip", "reason": "x"},
                )
            ]
        )
    )
    await run_background_turn(
        streamer,
        wake_source="idle_timer",
        system_prompt="MY CUSTOM HEARTBEAT PROMPT",
        now=_now,
    )
    assert len(streamer.calls) == 1
    sent_text = streamer.calls[0][0].text
    assert "MY CUSTOM HEARTBEAT PROMPT" in sent_text
    assert "idle_timer" in sent_text


async def test_runner_falls_back_to_bundled_prompt_when_none(tmp_path: Any) -> None:
    """When no override path/string is given, the bundled prompt is used."""
    from dream.wake._prompt import BUNDLED_HEARTBEAT_PROMPT

    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "skip", "reason": "x"},
                )
            ]
        )
    )
    await run_background_turn(streamer, wake_source="cron", now=_now)
    sent_text = streamer.calls[0][0].text
    assert BUNDLED_HEARTBEAT_PROMPT.strip().splitlines()[0] in sent_text


# --- bounded by exactly one model turn -------------------------------------


async def test_runner_drives_exactly_one_turn() -> None:
    """The runner never re-enters the model — wake is single-turn by design."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "run", "tasks": ["x"], "reason": "y"},
                )
            ]
        )
    )
    await run_background_turn(streamer, wake_source="idle_timer", now=_now)
    assert len(streamer.calls) == 1


async def test_runner_default_now_uses_utc() -> None:
    """When ``now`` is omitted, the decision's ``decided_at`` is tz-aware UTC."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "skip", "reason": "x"},
                )
            ]
        )
    )
    before = datetime.now(UTC)
    decision = await run_background_turn(streamer, wake_source="cron")
    after = datetime.now(UTC)
    assert decision.decided_at.tzinfo is not None
    assert before <= decision.decided_at <= after


# --- input shape ------------------------------------------------------------


def test_run_background_turn_is_async() -> None:
    """Smoke: importable + actually a coroutine factory, no sync surprise."""
    import inspect

    assert inspect.iscoroutinefunction(run_background_turn)
