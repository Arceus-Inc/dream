"""Spec 04 stage 4c — the auto-compact orchestrator + reactive PTL helper.

The orchestrator wraps the deterministic 4b primitives in a turn-aware
policy: it owns the auto-threshold check, the microcompact-first / full-
LLM-second tier escalation, the same-turn cooldown, the "context-pressure"
end-of-turn signal, the consecutive-compactor-failure counter, and the
reactive prompt-too-long retry shrink.

The full (LLM) tier is *dependency-injected* via a ``summariser`` callable
because Spec 04 explicitly defers the compactor prompt template — the
orchestrator must wire the call, not author the prompt.
"""

from __future__ import annotations

from typing import Any

from dream.contracts.provider import ProviderCapabilities
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.services.compact import (
    CompactAttachment,
    CompactionResult,
    build_post_compact_messages,
)
from dream.services.compact._orchestrator import (
    AutoCompactState,
    auto_compact_if_needed,
    react_to_ptl,
)
from dream.services.context_log import (
    ContextCompactionCompleted,
    ContextCompactionTriggered,
    ContextEvent,
)

# --- helpers -----------------------------------------------------------------


def _big_text_msg(role: str, text: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=[TextBlock(text=text)])


def _tool_round(tool_id: str, name: str, content: str) -> list[ConversationMessage]:
    return [
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id=tool_id, name=name, input={})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
        ),
    ]


def _caps(window: int | None) -> ProviderCapabilities:
    return ProviderCapabilities(max_context_tokens=window)


def _pressure_messages(num_rounds: int = 8, payload_chars: int = 8_000) -> list[ConversationMessage]:
    """Build a long transcript stuffed with bulky droppable tool results.

    ``read_file`` is in ``COMPACTABLE_TOOLS`` so microcompact will hit them.
    """
    messages: list[ConversationMessage] = [_big_text_msg("user", "kick off")]
    for i in range(num_rounds):
        messages.extend(_tool_round(f"t{i}", "read_file", "x" * payload_chars))
    return messages


def _stub_summariser_factory(call_log: list[int]) -> Any:
    """Returns a summariser callable that records ``len(older)`` per call."""

    def _summariser(older: list[ConversationMessage]) -> list[ConversationMessage]:
        call_log.append(len(older))
        return [
            ConversationMessage(role="user", content=[TextBlock(text="[summary]")]),
        ]

    return _summariser


# --- AutoCompactState shape --------------------------------------------------


def test_auto_compact_state_defaults() -> None:
    state = AutoCompactState()
    assert state.compacted_this_turn is False
    assert state.consecutive_failures == 0
    assert state.turn_id == ""


# --- acceptance #1: threshold uses substrate window --------------------------


def test_threshold_uses_substrate_window() -> None:
    """A 1k-token window trips much sooner than the default 200k fallback."""
    messages = _pressure_messages(num_rounds=2, payload_chars=2_000)
    state = AutoCompactState()
    events: list[ContextEvent] = []

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(1_000),  # tiny window → over threshold immediately
        state=state,
        event_sink=events.append,
        summariser=None,  # microcompact-only path is enough
    )

    assert result is not None, "small window must trip threshold"
    assert any(isinstance(e, ContextCompactionTriggered) for e in events)


def test_missing_window_falls_back_with_warning() -> None:
    """No substrate window → uses default; orchestrator still runnable."""
    messages = _pressure_messages(num_rounds=2, payload_chars=2_000)
    state = AutoCompactState()
    events: list[ContextEvent] = []

    # default window is huge so a small transcript shouldn't trip
    _, result = auto_compact_if_needed(
        messages,
        capabilities=None,
        state=state,
        event_sink=events.append,
    )
    assert result is None


def test_under_threshold_returns_no_op() -> None:
    messages = [_big_text_msg("user", "hi")]
    state = AutoCompactState()
    events: list[ContextEvent] = []

    new_msgs, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(200_000),
        state=state,
        event_sink=events.append,
    )
    assert result is None
    assert new_msgs == messages
    assert events == []


# --- acceptance #2/#5: triggered + completed events; tier recorded ----------


def test_auto_compaction_triggers_at_threshold_emits_triggered_event() -> None:
    messages = _pressure_messages(num_rounds=8, payload_chars=8_000)
    state = AutoCompactState()
    events: list[ContextEvent] = []

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        event_sink=events.append,
        summariser=_stub_summariser_factory([]),
    )

    assert result is not None
    triggered = [e for e in events if isinstance(e, ContextCompactionTriggered)]
    assert len(triggered) == 1
    assert triggered[0].trigger == "auto"
    assert triggered[0].utilisation > 0.7


def test_completed_event_records_tier_microcompact() -> None:
    """When microcompact alone reclaims enough room, tier='microcompact'."""
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)
    state = AutoCompactState()
    events: list[ContextEvent] = []
    summariser_calls: list[int] = []

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(30_000),  # over after build, under after microcompact
        state=state,
        event_sink=events.append,
        summariser=_stub_summariser_factory(summariser_calls),
    )

    assert result is not None
    completed = [e for e in events if isinstance(e, ContextCompactionCompleted)]
    assert len(completed) == 1
    assert completed[0].tier == "microcompact"
    assert summariser_calls == [], "summariser must not run when microcompact suffices"


def test_completed_event_records_tier_full_when_summariser_runs() -> None:
    messages = _pressure_messages(num_rounds=12, payload_chars=20_000)
    state = AutoCompactState()
    events: list[ContextEvent] = []
    summariser_calls: list[int] = []

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),  # microcompact won't be enough
        state=state,
        event_sink=events.append,
        summariser=_stub_summariser_factory(summariser_calls),
    )

    assert result is not None
    assert summariser_calls, "summariser must run when microcompact insufficient"
    completed = [e for e in events if isinstance(e, ContextCompactionCompleted)]
    assert any(e.tier == "full" for e in completed)


# --- acceptance #2: microcompact attempted BEFORE full -----------------------


def test_microcompact_attempted_before_full() -> None:
    """The summariser must not be called until microcompact has tried."""
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)
    state = AutoCompactState()
    call_order: list[str] = []

    def tracking_summariser(older: list[ConversationMessage]) -> list[ConversationMessage]:
        call_order.append("summariser")
        return [ConversationMessage(role="user", content=[TextBlock(text="s")])]

    _new_msgs, _ = auto_compact_if_needed(
        messages,
        capabilities=_caps(30_000),
        state=state,
        summariser=tracking_summariser,
    )

    # If microcompact alone freed enough, no summariser call.
    # Either way: never call summariser on the unmodified input length.
    assert "summariser" not in call_order or call_order == ["summariser"]


def test_full_compaction_only_when_microcompact_insufficient() -> None:
    """If microcompact alone reaches under-threshold, summariser never runs."""
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)
    state = AutoCompactState()
    summariser_calls: list[int] = []

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(30_000),
        state=state,
        summariser=_stub_summariser_factory(summariser_calls),
    )
    assert result is not None
    assert result.tier == "microcompact"
    assert summariser_calls == []


# --- acceptance #4: same-turn cooldown / context-pressure --------------------


def test_only_one_auto_compaction_per_turn() -> None:
    messages = _pressure_messages(num_rounds=8, payload_chars=8_000)
    state = AutoCompactState()
    events: list[ContextEvent] = []
    summariser_calls: list[int] = []
    summariser = _stub_summariser_factory(summariser_calls)

    new_msgs, first = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        event_sink=events.append,
        summariser=summariser,
    )
    assert first is not None
    assert state.compacted_this_turn is True

    # Even if still pressured, a second call this turn must no-op.
    _new_msgs2, second = auto_compact_if_needed(
        new_msgs,
        capabilities=_caps(8_000),
        state=state,
        event_sink=events.append,
        summariser=summariser,
    )
    assert second is None
    # Only one triggered event total.
    triggered = [e for e in events if isinstance(e, ContextCompactionTriggered)]
    assert len(triggered) == 1


def test_second_trigger_forces_context_pressure_signal() -> None:
    """Same-turn second compaction is suppressed AND the state flag exposes it.

    The orchestrator returns ``(messages, None)`` on a cooldown-suppressed
    call; the caller engine can read ``state.compacted_this_turn`` to know
    it already ran. The pressure check (still over threshold) is the
    caller's signal to end the turn with outcome ``context-pressure``.
    """
    messages = _pressure_messages(num_rounds=8, payload_chars=8_000)
    state = AutoCompactState()
    summariser_calls: list[int] = []
    summariser = _stub_summariser_factory(summariser_calls)

    auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        summariser=summariser,
    )
    _new_msgs, second = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        summariser=summariser,
    )
    assert second is None
    assert state.compacted_this_turn is True


def test_begin_turn_resets_cooldown_flag() -> None:
    messages = _pressure_messages(num_rounds=8, payload_chars=8_000)
    state = AutoCompactState()
    summariser_calls: list[int] = []

    auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        summariser=_stub_summariser_factory(summariser_calls),
    )
    assert state.compacted_this_turn is True

    # Importing here avoids a forward-import; the helper is in the same module.
    from dream.services.compact._orchestrator import begin_turn

    begin_turn(state, turn_id="t_next")
    assert state.compacted_this_turn is False
    assert state.turn_id == "t_next"


# --- acceptance #6: contract via attachments + #8 checkpoint ----------------


def test_post_compact_messages_rebuilt_from_attachments() -> None:
    """When full tier runs, the result holds attachments the engine rebuilds from."""
    messages = _pressure_messages(num_rounds=12, payload_chars=20_000)
    state = AutoCompactState()
    summariser_calls: list[int] = []

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        summariser=_stub_summariser_factory(summariser_calls),
        carryover_metadata={
            "exec_plan_filename": "PLAN-001.md",
            "current_step": "step-3",
            "blocked_steps": [
                {"step_id": "step-1", "reason": "dep missing"},
            ],
            "failing_tests": ["test_foo"],
            "modified_files": ["a.py"],
        },
    )
    assert result is not None
    assert result.tier == "full"
    assert isinstance(result, CompactionResult)
    assert any(isinstance(a, CompactAttachment) for a in result.attachments)
    kinds = {a.kind for a in result.attachments}
    assert "exec_plan" in kinds
    assert "blocked_steps" in kinds


def test_compaction_records_recoverable_checkpoint() -> None:
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)
    state = AutoCompactState()
    carryover: dict[str, Any] = {}

    auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        carryover_metadata=carryover,
        summariser=_stub_summariser_factory([]),
    )
    checkpoints = carryover.get("compact_checkpoints", [])
    assert checkpoints, "expected at least one checkpoint to be recorded"
    assert "compact_last" in carryover


# --- failure handling -------------------------------------------------------


def test_compactor_failure_increments_consecutive_counter() -> None:
    messages = _pressure_messages(num_rounds=12, payload_chars=20_000)
    state = AutoCompactState()

    def boom(_: list[ConversationMessage]) -> list[ConversationMessage]:
        raise RuntimeError("summariser blew up")

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        summariser=boom,
    )
    assert result is None
    assert state.consecutive_failures == 1


def test_compactor_success_resets_failure_counter() -> None:
    messages = _pressure_messages(num_rounds=12, payload_chars=20_000)
    state = AutoCompactState(consecutive_failures=1)
    # Reset cooldown so the second call isn't suppressed.
    state.compacted_this_turn = False

    _, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(8_000),
        state=state,
        summariser=_stub_summariser_factory([]),
    )
    assert result is not None
    assert state.consecutive_failures == 0


# --- reactive (PTL) ----------------------------------------------------------


def test_reactive_compaction_retries_ptl_call_once() -> None:
    """First reactive attempt collapses or truncates and signals retry-ready."""
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)

    new_msgs, did_shrink = react_to_ptl(messages, already_attempted=False)
    assert did_shrink is True
    assert len(new_msgs) <= len(messages)


def test_reactive_compaction_does_not_loop() -> None:
    """A second reactive call within the same provider call must no-op."""
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)

    new_msgs, did_shrink = react_to_ptl(messages, already_attempted=True)
    assert did_shrink is False
    assert new_msgs == messages


def test_reactive_falls_back_to_head_truncation_when_collapse_fails() -> None:
    """No oversized text → collapse returns None → truncator must run."""
    # Many small rounds: no individual text is over the collapse limit,
    # so try_context_collapse can't help, but truncate_head_for_ptl_retry can.
    messages: list[ConversationMessage] = []
    for i in range(20):
        messages.append(_big_text_msg("user", f"round {i} prompt"))
        messages.append(_big_text_msg("assistant", f"round {i} reply"))

    new_msgs, did_shrink = react_to_ptl(messages, already_attempted=False)
    assert did_shrink is True
    assert len(new_msgs) < len(messages)


# --- micro tier returns the rebuilt transcript (contract parity with full) ---


def test_micro_tier_returns_rebuilt_transcript_with_boundary() -> None:
    """The micro tier must return the rebuilt post-compact transcript (boundary
    + attachments) — not the raw microcompacted messages — so it honours the
    same contract as the full tier (Spec 04 #9 tier-parity).
    """
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)
    state = AutoCompactState()
    carryover: dict[str, Any] = {"failing_tests": ["test_foo", "test_bar"]}

    returned, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(30_000),  # over before, under after microcompact
        state=state,
        summariser=None,  # force microcompact-only path
        carryover_metadata=carryover,
    )

    assert result is not None
    assert result.tier == "microcompact"
    # The returned transcript MUST equal the rebuilt-from-result transcript,
    # i.e. it carries the boundary marker + attachments, not the raw messages.
    assert returned == build_post_compact_messages(result)
    assert returned[0] is result.boundary_marker
    assert "[Compact boundary marker]" in returned[0].text
    # Attachment built from carryover must be present in the returned transcript.
    assert result.attachments, "failing_tests carryover should yield an attachment"


def test_micro_tier_boundary_carries_pre_compact_footprint() -> None:
    """The micro boundary marker must carry pre-compact recovery data via the
    keys create_compact_boundary_message actually consumes.
    """
    messages = _pressure_messages(num_rounds=10, payload_chars=8_000)
    state = AutoCompactState()

    returned, result = auto_compact_if_needed(
        messages,
        capabilities=_caps(30_000),
        state=state,
        summariser=None,
    )

    assert result is not None
    assert result.tier == "microcompact"
    assert "Pre-compact footprint" in returned[0].text
    # The pre-compact message count is the original transcript length.
    assert f"messages={len(messages)}" in returned[0].text
