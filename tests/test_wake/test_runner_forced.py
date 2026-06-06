"""Spec 06.5 slice 2 — forced-mode wake turn (anti-coma guard).

When ``skip_streak >= max_consecutive_skips`` the next background turn
is constructed in *forced* mode: the system prompt picks up an addendum
that tells the model its last N skips were declined, the heartbeat
tool's ``action`` enum is narrowed to ``["run"]``, and the runner
synthesises a minimal ``run`` decision if the model still produces no
tool call.

Together: the depressed agent can never sleep forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

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
from dream.wake._source import IdleTimerWake
from dream.wake._tool import ForcedHeartbeatInput, HeartbeatInput

# --- local fake streamer (mirrors test_runner.py shape) --------------------


@dataclass
class _ScriptedTurn:
    text_chunks: list[str] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)


class _ScriptedStreamer:
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


# --- forced model schema ---------------------------------------------------


def test_forced_heartbeat_input_narrows_action_to_run_only() -> None:
    """The forced-mode pydantic model is what slice 3's REPL wiring will
    advertise to the model when ``forced=True``."""
    parsed = ForcedHeartbeatInput.model_validate(
        {"action": "run", "tasks": [], "reason": "anti-coma"}
    )
    assert parsed.action == "run"


def test_forced_heartbeat_input_rejects_skip_action() -> None:
    """``action="skip"`` is not in the forced enum — the wire schema would
    reject it."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ForcedHeartbeatInput.model_validate(
            {"action": "skip", "tasks": [], "reason": "want to skip"}
        )


def test_forced_action_literal_excludes_skip() -> None:
    """Type-level proof: the action field is ``Literal["run"]``, not
    ``Literal["skip", "run"]``."""
    from typing import get_args, get_type_hints

    hints = get_type_hints(ForcedHeartbeatInput)
    assert get_args(hints["action"]) == ("run",)


def test_regular_heartbeat_input_still_accepts_skip() -> None:
    """Sanity: narrowing the *forced* model doesn't break the normal one."""
    parsed = HeartbeatInput.model_validate(
        {"action": "skip", "tasks": [], "reason": "nothing pending"}
    )
    assert parsed.action == "skip"


# --- prompt augmentation in forced mode ------------------------------------


async def test_forced_mode_adds_anti_coma_addendum_to_prompt() -> None:
    """The runner, when ``forced=True``, splices the anti-coma instruction
    into the stimulus the model sees."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "run", "tasks": ["wake"], "reason": "ok"},
                )
            ]
        )
    )
    await run_background_turn(
        streamer,
        wake_source=IdleTimerWake(idle_minutes=120),
        forced=True,
        now=_now,
    )
    sent = streamer.calls[0][0].text
    # The wording isn't pinned, but it MUST contain the words "skip" and
    # something about being declined / forced — otherwise the model can't
    # tell it's in anti-coma mode.
    lowered = sent.lower()
    assert "skip" in lowered
    assert "declined" in lowered or "forced" in lowered


async def test_non_forced_mode_omits_addendum() -> None:
    """Negative: when ``forced=False`` the addendum is not present."""
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
        wake_source=IdleTimerWake(idle_minutes=10),
        forced=False,
        now=_now,
    )
    sent = streamer.calls[0][0].text.lower()
    assert "declined" not in sent


# --- forced-mode outcomes ---------------------------------------------------


async def test_forced_mode_synthesises_run_when_model_silent() -> None:
    """Spec acceptance criterion 14: forced + silent model = synthesised
    ``run`` with empty tasks + ``forced=True`` flag."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(text_chunks=["I'd rather not, thanks."])
    )
    decision = await run_background_turn(
        streamer,
        wake_source=IdleTimerWake(idle_minutes=120),
        forced=True,
        now=_now,
    )
    assert decision.action == "run"
    assert decision.tasks == ()
    assert decision.forced is True
    assert "forced" in decision.reason.lower()
    # Synthesised decisions are still "decided" outcomes — not "missing".
    assert decision.outcome == "decided"


async def test_forced_mode_synthesises_run_when_model_emits_skip() -> None:
    """Defensive: if the model defies the narrowed schema and emits a
    ``skip`` anyway, the runner overrides it with a forced run."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "skip", "reason": "i still want to skip"},
                )
            ]
        )
    )
    decision = await run_background_turn(
        streamer,
        wake_source=IdleTimerWake(idle_minutes=120),
        forced=True,
        now=_now,
    )
    assert decision.action == "run"
    assert decision.forced is True
    assert decision.tasks == ()


async def test_forced_mode_honours_run_decision_from_model() -> None:
    """When the forced model picks ``run`` honestly, its tasks/reason are
    kept — but the decision still records ``forced=True``."""
    streamer = _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={
                        "action": "run",
                        "tasks": ["check inbox"],
                        "reason": "queue is non-empty",
                    },
                )
            ]
        )
    )
    decision = await run_background_turn(
        streamer,
        wake_source=IdleTimerWake(idle_minutes=120),
        forced=True,
        now=_now,
    )
    assert decision.action == "run"
    assert decision.tasks == ("check inbox",)
    assert decision.reason == "queue is non-empty"
    assert decision.forced is True


async def test_forced_synthesised_decision_carries_wake_source() -> None:
    """The wake source threads through to the synthesised record too."""
    streamer = _ScriptedStreamer(_ScriptedTurn(text_chunks=[""]))
    src = IdleTimerWake(idle_minutes=999)
    decision = await run_background_turn(
        streamer, wake_source=src, forced=True, now=_now
    )
    assert decision.wake_source == src


# --- non-forced silence is still ``missing`` -------------------------------


async def test_non_forced_silence_still_yields_missing() -> None:
    """Sanity: forced behaviour is opt-in. Normal silence stays ``missing``
    so the orchestrator can decide not to advance the skip counter."""
    streamer = _ScriptedStreamer(_ScriptedTurn(text_chunks=["mmm"]))
    decision = await run_background_turn(
        streamer,
        wake_source=IdleTimerWake(idle_minutes=10),
        forced=False,
        now=_now,
    )
    assert decision.outcome == "missing"
    assert decision.action == "skip"
    assert decision.forced is False


def test_decision_includes_forced_field_for_record() -> None:
    d = HeartbeatDecision(
        decided_at=_now(),
        action="run",
        tasks=(),
        reason="forced after 5 skips",
        wake_source=IdleTimerWake(idle_minutes=300),
        forced=True,
    )
    assert d.forced is True
