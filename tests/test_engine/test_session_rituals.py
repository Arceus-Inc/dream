"""Spec 03 stage 3b — ``run_session`` orchestrator with rituals.

Stage 3a covered the deterministic machinery (FSM, timeouts, records,
crash resume, hook bus, checkpoints). This file covers the rituals
that stage 3b layers on top:

- **Orientation** (#3, #15): gather + optional LLM summary; brief prepended
  to the first turn; runs once per session; blocking findings abort the
  session before ``orienting -> working``.
- **Heartbeat / coma** (#11/#12): when health fails ``threshold`` times in
  a row during a turn, cancel the turn, write a synthetic record with
  ``outcome="aborted"``, and end the session as
  ``outcome="aborted", reason="coma"``.
- **Reviewer** (#13): after the user messages are exhausted, run the
  Ralph-Wiggum loop; ``accept`` -> seal as ``done``; ``request_changes``
  -> inject items as a user message and re-enter ``working``; at
  ``max_rounds`` request_changes -> force-close as ``done-with-warnings``
  with the unresolved items carried in ``reason``.

All three subsystems are opt-in via ``SessionConfig`` fields that default
to ``None`` — every 3a session test must still pass unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
)
from dream.engine._heartbeat import HeartbeatConfig
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from dream.engine._orientation import (
    OrientationBrief,
    OrientationConfig,
    ValidatorFinding,
)
from dream.engine._records import SessionEnd, TurnRecord
from dream.engine._reviewer import ReviewerConfig, ReviewerOutcome
from dream.engine._session import (
    SessionConfig,
    SessionEvent,
    run_session,
)
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn

# --- helpers ----------------------------------------------------------------


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _ticking_clock(start_seconds: int = 0, step_seconds: int = 1):
    base = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
    counter = [start_seconds]

    def now() -> datetime:
        t = base + timedelta(seconds=counter[0])
        counter[0] += step_seconds
        return t

    return now


def _config(
    streamer: FakeStreamer,
    *,
    tools: FakeDispatcher | None = None,
    turn_timeout_seconds: float | None = None,
    orientation: OrientationConfig | None = None,
    heartbeat: HeartbeatConfig | None = None,
    reviewer: ReviewerConfig | None = None,
) -> SessionConfig:
    return SessionConfig(
        client=streamer,
        tools=tools or FakeDispatcher(),
        max_turns=4,
        turn_timeout_seconds=turn_timeout_seconds,
        session_id="s_b",
        now=_ticking_clock(),
        orientation=orientation,
        heartbeat=heartbeat,
        reviewer=reviewer,
    )


async def _drain(
    config: SessionConfig,
    user_messages: list[ConversationMessage],
    *,
    resume_messages: list[ConversationMessage] | None = None,
) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    async for ev in run_session(
        config, user_messages, resume_messages=resume_messages
    ):
        events.append(ev)
    return events


# --- orientation integration -----------------------------------------------


async def test_orientation_brief_is_prepended_before_first_turn() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    brief = OrientationBrief(
        repo_summary="dream",
        progress_tail="last beat",
        active_exec_plan="stage 3b",
    )

    async def gather() -> OrientationBrief:
        return brief

    config = _config(
        streamer,
        orientation=OrientationConfig(gather=gather),
    )
    await _drain(config, [_user("hello")])

    assert len(streamer.calls) == 1
    first_call_messages = streamer.calls[0]
    # Two messages: the orientation brief, then the user message.
    assert len(first_call_messages) == 2
    assert "dream" in first_call_messages[0].text
    assert first_call_messages[1].text == "hello"


async def test_orientation_runs_exactly_once_per_session() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["a"]),
            FakeTurn(text_chunks=["b"]),
            FakeTurn(text_chunks=["c"]),
        ]
    )
    calls = 0

    async def gather() -> OrientationBrief:
        nonlocal calls
        calls += 1
        return OrientationBrief(
            repo_summary="r", progress_tail="p", active_exec_plan="x"
        )

    config = _config(streamer, orientation=OrientationConfig(gather=gather))
    await _drain(
        config, [_user("m1"), _user("m2"), _user("m3")]
    )

    assert calls == 1
    # Brief appears in transcript only once; later turns should still see
    # it because it's been prepended once and stays in the transcript.
    for call in streamer.calls:
        # All calls share the same prefixed brief
        assert "r" in call[0].text


async def test_orientation_summariser_runs_when_configured() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    seen: list[str] = []

    async def gather() -> OrientationBrief:
        return OrientationBrief(
            repo_summary="repo", progress_tail="p", active_exec_plan="x"
        )

    async def summarise(b: OrientationBrief) -> str:
        seen.append(b.repo_summary)
        return "a one-paragraph summary"

    config = _config(
        streamer,
        orientation=OrientationConfig(gather=gather, summariser=summarise),
    )
    await _drain(config, [_user("hello")])

    assert seen == ["repo"]
    # The summary text appears in the prepended message.
    assert "a one-paragraph summary" in streamer.calls[0][0].text


async def test_blocking_validator_finding_aborts_before_first_turn() -> None:
    streamer = FakeStreamer(turns=[])  # no turns should ever run

    async def gather() -> OrientationBrief:
        return OrientationBrief(
            repo_summary="r",
            progress_tail="p",
            active_exec_plan="x",
            validator_findings=(
                ValidatorFinding("blocking", "V-100", "config invalid"),
            ),
        )

    config = _config(streamer, orientation=OrientationConfig(gather=gather))
    events = await _drain(config, [_user("hello")])

    assert streamer.calls == []
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert len(ends) == 1
    assert ends[0].outcome == "aborted"
    assert ends[0].reason == "validator-blocking"
    assert ends[0].turns == 0


async def test_no_orientation_config_behaves_like_stage_3a() -> None:
    """Sanity: leaving orientation=None must not add any transcript prefix."""
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    config = _config(streamer, orientation=None)
    await _drain(config, [_user("hello")])

    assert len(streamer.calls) == 1
    # Single message, the user message itself.
    assert len(streamer.calls[0]) == 1
    assert streamer.calls[0][0].text == "hello"


async def test_orientation_skipped_on_resume() -> None:
    """A resumed session inherits the prior incarnation's orientation;
    re-orienting would violate ``orientation runs exactly once`` and would
    pollute the resumed transcript with a duplicate brief.
    """
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    calls = 0

    async def gather() -> OrientationBrief:
        nonlocal calls
        calls += 1
        return OrientationBrief(
            repo_summary="r", progress_tail="p", active_exec_plan="x"
        )

    config = _config(streamer, orientation=OrientationConfig(gather=gather))
    resume = [
        _user("prior msg"),
        ConversationMessage(
            role="assistant", content=[TextBlock(text="prior reply")]
        ),
    ]
    await _drain(config, [_user("new")], resume_messages=resume)

    assert calls == 0


# --- heartbeat / coma integration ------------------------------------------


async def test_heartbeat_healthy_does_not_disturb_session() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])

    async def health() -> bool:
        return True

    config = _config(
        streamer,
        heartbeat=HeartbeatConfig(
            health=health, interval_seconds=0.01, failure_threshold=3
        ),
    )
    events = await _drain(config, [_user("m1")])

    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert ends[0].outcome == "done"


async def test_heartbeat_coma_during_turn_aborts_session() -> None:
    # A slow turn (50ms) gives the heartbeat (interval 5ms, threshold 3)
    # plenty of time to trip while the LLM is "in flight".
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["slow"], delay=0.2)]
    )

    async def health() -> bool:
        return False

    config = _config(
        streamer,
        heartbeat=HeartbeatConfig(
            health=health, interval_seconds=0.005, failure_threshold=3
        ),
    )
    events = await _drain(config, [_user("m1")])

    records = [e for e in events if isinstance(e, TurnRecord)]
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert len(records) == 1
    assert records[0].outcome == "aborted"
    assert len(ends) == 1
    assert ends[0].outcome == "aborted"
    assert ends[0].reason == "coma"


async def test_no_heartbeat_config_behaves_like_stage_3a() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    config = _config(streamer, heartbeat=None)
    events = await _drain(config, [_user("m1")])
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert ends[0].outcome == "done"


# --- reviewer integration ---------------------------------------------------


class _ScriptedReviewer:
    """Returns scripted verdicts in order; records every review() call."""

    def __init__(self, verdicts: Sequence[ReviewerOutcome]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[list[ConversationMessage]] = []

    async def review(
        self, transcript: list[ConversationMessage]
    ) -> ReviewerOutcome:
        self.calls.append(list(transcript))
        if not self._verdicts:
            raise AssertionError(
                "_ScriptedReviewer ran out of verdicts; orchestrator called "
                "review() more times than the test scripted"
            )
        return self._verdicts.pop(0)


async def test_reviewer_accept_on_first_review_seals_as_done() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["work"])])
    reviewer = _ScriptedReviewer([ReviewerOutcome(verdict="accept")])
    config = _config(
        streamer, reviewer=ReviewerConfig(reviewer=reviewer, max_rounds=3)
    )
    events = await _drain(config, [_user("m1")])

    assert len(reviewer.calls) == 1
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert len(ends) == 1
    assert ends[0].outcome == "done"
    assert ends[0].reason is None
    # Only the initial user-driven turn ran.
    assert len(streamer.calls) == 1


async def test_reviewer_request_changes_injects_items_and_drives_another_turn() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["first"]),
            FakeTurn(text_chunks=["second"]),
        ]
    )
    reviewer = _ScriptedReviewer(
        [
            ReviewerOutcome(
                verdict="request_changes", items=("please tighten test names",)
            ),
            ReviewerOutcome(verdict="accept"),
        ]
    )
    config = _config(
        streamer, reviewer=ReviewerConfig(reviewer=reviewer, max_rounds=3)
    )
    events = await _drain(config, [_user("m1")])

    # Two work turns: the user-driven one and one driven by the reviewer.
    assert len(streamer.calls) == 2
    # The injected items appear in the second turn's transcript prefix.
    second_call_text = " ".join(m.text for m in streamer.calls[1])
    assert "please tighten test names" in second_call_text
    # Two reviewer calls (one per work turn).
    assert len(reviewer.calls) == 2
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert ends[0].outcome == "done"
    # Both turns recorded.
    records = [e for e in events if isinstance(e, TurnRecord)]
    assert [r.turn_number for r in records] == [1, 2]


async def test_reviewer_max_rounds_request_changes_force_closes_with_warnings() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["t1"]),
            FakeTurn(text_chunks=["t2"]),
            FakeTurn(text_chunks=["t3"]),
        ]
    )
    reviewer = _ScriptedReviewer(
        [
            ReviewerOutcome(verdict="request_changes", items=("A",)),
            ReviewerOutcome(verdict="request_changes", items=("B",)),
            ReviewerOutcome(verdict="request_changes", items=("C",)),
        ]
    )
    config = _config(
        streamer, reviewer=ReviewerConfig(reviewer=reviewer, max_rounds=3)
    )
    events = await _drain(config, [_user("m1")])

    # 3 reviewer calls, on the 3rd we force-close.
    assert len(reviewer.calls) == 3
    # Work turns: initial + 2 driven by rounds 1 and 2 (round 3 does not
    # re-enter; instead it triggers force close).
    assert len(streamer.calls) == 3
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert len(ends) == 1
    assert ends[0].outcome == "done-with-warnings"
    # Unresolved items carried in reason (the last verdict's items).
    assert ends[0].reason is not None
    assert "C" in ends[0].reason


async def test_no_reviewer_config_behaves_like_stage_3a() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    config = _config(streamer, reviewer=None)
    events = await _drain(config, [_user("m1")])
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert ends[0].outcome == "done"
    # Only one turn — no reviewer-driven re-entry.
    assert len([e for e in events if isinstance(e, TurnRecord)]) == 1


async def test_reviewer_skipped_when_no_turns_ran() -> None:
    """If no user messages were provided the agent has done no work for
    the reviewer to review; calling the reviewer would be meaningless and
    its outcome would not map cleanly to a SessionOutcome.
    """
    streamer = FakeStreamer(turns=[])
    reviewer = _ScriptedReviewer([ReviewerOutcome(verdict="accept")])
    config = _config(
        streamer, reviewer=ReviewerConfig(reviewer=reviewer, max_rounds=3)
    )
    events = await _drain(config, [])

    assert reviewer.calls == []
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert ends[0].outcome == "done"
    assert ends[0].turns == 0


# --- combined ---------------------------------------------------------------


async def test_orientation_brief_visible_in_reviewer_driven_followup_turn() -> None:
    """Once the brief is prepended it stays through reviewer-driven turns."""
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["first"]),
            FakeTurn(text_chunks=["second"]),
        ]
    )

    async def gather() -> OrientationBrief:
        return OrientationBrief(
            repo_summary="dream-orient",
            progress_tail="p",
            active_exec_plan="x",
        )

    reviewer = _ScriptedReviewer(
        [
            ReviewerOutcome(verdict="request_changes", items=("nit",)),
            ReviewerOutcome(verdict="accept"),
        ]
    )
    config = _config(
        streamer,
        orientation=OrientationConfig(gather=gather),
        reviewer=ReviewerConfig(reviewer=reviewer, max_rounds=3),
    )
    await _drain(config, [_user("m1")])

    # Both turns see the orientation prefix.
    assert "dream-orient" in streamer.calls[0][0].text
    assert "dream-orient" in streamer.calls[1][0].text


# --- silence collection-time unused warnings --------------------------------

# Some imports are referenced only via test bodies' type checks; keep the
# module re-export surface minimal but stable.
_ = (
    AssistantTextDelta,
    AssistantTurnComplete,
    AsyncIterator,
    ContentBlock,
    StreamEvent,
    ToolUseBlock,
    UsageSnapshot,
)
