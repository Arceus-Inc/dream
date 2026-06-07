"""Lock-in tests for the CodeAnt review fixes (PRs #20-33, wake package).

Each test targets one finding and is written to FAIL without the fix:

- runner closes the turn stream on early break (#42)
- override prompt is sent verbatim, not ``rstrip``-ed (#45)
- ``decided_at`` is captured after the turn, not before (#46)
- ``BUNDLED_HEARTBEAT_PROMPT`` is importable from ``dream.wake`` (#43)
- the published JSON schema carries the per-task 200-char cap (#44)
- ``read_state`` returns defaults on PermissionError / decode error (#47)
- an observer exception does not abort the wake cycle (#48)
- a non-dict ``wake_source`` in a jsonl record raises ``ValueError`` (#49)
- a non-int ``idle_minutes`` raises instead of being coerced (#50)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._events import AssistantTurnComplete, StreamEvent
from dream.engine._messages import ContentBlock, ConversationMessage, ToolUseBlock
from dream.wake import (
    BUNDLED_HEARTBEAT_PROMPT,
    HeartbeatConfig,
    HeartbeatTool,
    run_background_turn,
    run_wake_cycle,
)
from dream.wake._decision import from_jsonl_line
from dream.wake._source import CronWake, IdleTimerWake, ManualWake, wake_source_from_dict
from dream.wake._state import HeartbeatState, read_state, write_state
from dream.wake._tool import HeartbeatInput

# --- shared scripted streamer ----------------------------------------------


@dataclass
class _ScriptedTurn:
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)


class _ClosableStreamer:
    """Records whether the per-turn async generator was ``aclose``-d.

    The generator yields one ``AssistantTurnComplete`` then a trailing event
    the runner must never reach (it breaks first). ``aclose`` flips the flag
    via the generator's ``GeneratorExit``/finally.
    """

    def __init__(self, turn: _ScriptedTurn) -> None:
        self._turn = turn
        self.closed = False
        self.reached_after_break = False
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        try:
            blocks: list[ContentBlock] = list(self._turn.tool_uses)
            yield AssistantTurnComplete(blocks=blocks, usage=self._turn.usage)
            # Runner must break before consuming this; if it does, it means we
            # were fully drained rather than closed early.
            self.reached_after_break = True
            yield AssistantTurnComplete(blocks=[], usage=self._turn.usage)
        finally:
            self.closed = True


def _heartbeat_turn(**input_: Any) -> _ScriptedTurn:
    return _ScriptedTurn(
        tool_uses=[ToolUseBlock(id="tu_1", name="heartbeat", input=input_)]
    )


def _now() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


# --- #42: stream is aclosed on early break ---------------------------------


async def test_turn_stream_aclosed_on_early_break() -> None:
    streamer = _ClosableStreamer(
        _heartbeat_turn(action="skip", reason="x")
    )
    await run_background_turn(
        streamer, wake_source=ManualWake(), now=_now
    )
    assert streamer.closed is True
    # The runner broke on the first AssistantTurnComplete and never resumed.
    assert streamer.reached_after_break is False


# --- #45: override prompt sent verbatim ------------------------------------


class _CaptureStreamer:
    def __init__(self, turn: _ScriptedTurn) -> None:
        self._turn = turn
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        yield AssistantTurnComplete(
            blocks=list(self._turn.tool_uses), usage=self._turn.usage
        )


async def test_override_prompt_not_trimmed() -> None:
    # Trailing whitespace/newlines are operator-significant formatting.
    prompt = "OVERRIDE PROMPT BODY\n\n   "
    streamer = _CaptureStreamer(_heartbeat_turn(action="skip", reason="x"))
    await run_background_turn(
        streamer,
        wake_source=ManualWake(),
        system_prompt=prompt,
        now=_now,
    )
    sent = streamer.calls[0][0].text
    # The verbatim body (including its trailing whitespace) must survive.
    assert prompt in sent


# --- #46: decided_at captured after the turn -------------------------------


class _FlagStreamer:
    """Sets ``produced`` at the moment it hands the runner the decision event.

    The flag flips on the line *before* the ``yield`` returns control to the
    runner, so by the time the runner consumes the ``AssistantTurnComplete``
    the flag is already set.
    """

    def __init__(self, turn: _ScriptedTurn) -> None:
        self._turn = turn
        self.produced = False

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        del messages
        self.produced = True
        yield AssistantTurnComplete(
            blocks=list(self._turn.tool_uses), usage=self._turn.usage
        )


async def test_decided_at_captured_after_turn() -> None:
    """``now`` is read AFTER the model turn produces the decision.

    The clock records the streamer's ``produced`` flag at the moment it is
    called. Before the fix ``now()`` ran before the stream was even started
    (``produced is False``); after the fix it runs once the turn's events have
    been consumed.
    """
    streamer = _FlagStreamer(_heartbeat_turn(action="skip", reason="x"))
    seen_produced: list[bool] = []

    def _clock() -> datetime:
        seen_produced.append(streamer.produced)
        return _now()

    await run_background_turn(streamer, wake_source=ManualWake(), now=_clock)
    assert seen_produced, "now() was never called"
    # ``now()`` was called only after the turn produced its event. If the fix
    # regressed (now() before the stream), the flag would still be False.
    assert seen_produced[0] is True


# --- #43: BUNDLED_HEARTBEAT_PROMPT is public -------------------------------


def test_bundled_heartbeat_prompt_importable() -> None:
    import dream.wake as wake_pkg

    assert "BUNDLED_HEARTBEAT_PROMPT" in wake_pkg.__all__
    assert isinstance(BUNDLED_HEARTBEAT_PROMPT, str)
    assert BUNDLED_HEARTBEAT_PROMPT  # non-empty


# --- #44: per-task length cap is in the schema -----------------------------


def test_schema_advertises_per_task_length_limit() -> None:
    schema = HeartbeatInput.model_json_schema()
    schema_text = repr(schema)
    # The 200-char per-item cap must appear in the published schema, not only
    # in a post-init runtime check.
    assert "200" in schema_text
    assert "maxLength" in schema_text


def test_schema_uses_tool_input_model() -> None:
    # HeartbeatTool advertises HeartbeatInput, so the schema with the cap is
    # what the provider actually sees.
    assert HeartbeatTool.input_model is HeartbeatInput


def test_per_task_cap_still_enforced_at_runtime() -> None:
    with pytest.raises((ValueError, Exception)):
        HeartbeatInput.model_validate(
            {"action": "run", "tasks": ["x" * 201], "reason": "too long"}
        )


# --- #47: read_state forgiving on I/O + decode errors ----------------------


def test_read_state_returns_default_on_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "heartbeat.json"
    path.write_text("{}", encoding="utf-8")

    def _boom(*_: Any, **__: Any) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert read_state(path) == HeartbeatState()


def test_read_state_returns_default_on_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    # Invalid UTF-8 bytes -> UnicodeDecodeError on read_text.
    path.write_bytes(b"\xff\xfe\x00bad")
    assert read_state(path) == HeartbeatState()


def test_read_state_roundtrip_still_works(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    state = HeartbeatState(skip_streak=3)
    write_state(path, state)
    assert read_state(path) == state


# --- #48: observer exception does not abort the cycle ----------------------


async def test_observer_exception_does_not_abort_cycle(tmp_path: Path) -> None:
    streamer = _CaptureStreamer(_heartbeat_turn(action="run", tasks=["a"], reason="r"))

    def _bad_observer(_event: str, _payload: dict[str, Any]) -> None:
        raise RuntimeError("observer blew up")

    outcome = await run_wake_cycle(
        streamer,
        agent_id="agent-1",
        wake_source=ManualWake(),
        coordination_dir=tmp_path,
        config=HeartbeatConfig(),
        on_event=_bad_observer,
        now=_now,
    )
    # The decision and state update committed despite the failing observer.
    assert outcome.decision is not None
    assert outcome.decision.action == "run"
    # State was persisted (run resets streak to 0, last_decided_at set).
    state_files = list(tmp_path.glob("heartbeat-agent-1*.json"))
    assert state_files, "state should have been written"


# --- #49: non-dict wake_source raises --------------------------------------


def test_non_dict_wake_source_raises() -> None:
    import json

    line = json.dumps(
        {
            "kind": "heartbeat-decision",
            "decided_at": "2026-01-01T00:00:00+00:00",
            "action": "skip",
            "tasks": [],
            "reason": "x",
            "forced": False,
            "outcome": "decided",
            "wake_source": "manual",  # malformed: should be an object or null
        }
    )
    with pytest.raises(ValueError):
        from_jsonl_line(line)


def test_null_wake_source_still_allowed() -> None:
    import json

    line = json.dumps(
        {
            "kind": "heartbeat-decision",
            "decided_at": "2026-01-01T00:00:00+00:00",
            "action": "skip",
            "tasks": [],
            "reason": "x",
            "forced": False,
            "outcome": "decided",
            "wake_source": None,
        }
    )
    rec = from_jsonl_line(line)
    assert rec.wake_source is None


# --- #50: non-int idle_minutes raises --------------------------------------


def test_non_int_idle_minutes_raises_on_float() -> None:
    with pytest.raises(ValueError):
        wake_source_from_dict({"kind": "idle_timer", "idle_minutes": 10.5})


def test_non_int_idle_minutes_raises_on_bool() -> None:
    # bool is an int subclass — must still be rejected.
    with pytest.raises(ValueError):
        wake_source_from_dict({"kind": "idle_timer", "idle_minutes": True})


def test_non_int_idle_minutes_raises_on_str() -> None:
    with pytest.raises(ValueError):
        wake_source_from_dict({"kind": "idle_timer", "idle_minutes": "10"})


def test_int_idle_minutes_still_accepted() -> None:
    src = wake_source_from_dict({"kind": "idle_timer", "idle_minutes": 10})
    assert src == IdleTimerWake(idle_minutes=10)


def test_other_wake_sources_unaffected() -> None:
    assert wake_source_from_dict({"kind": "cron", "cron_kind": "x"}) == CronWake(
        cron_kind="x"
    )
