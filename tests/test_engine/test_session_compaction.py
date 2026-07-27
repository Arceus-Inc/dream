"""Spec 04 + Spec 05 slice E -- compaction wired into ``run_session``.

Acceptance pinned here:
- When ``SessionConfig.compactor`` is set and the transcript crosses the
  configured threshold, ``run_session`` runs ``auto_compact_if_needed``
  *before* the next turn, yielding exactly one ``CompactionDoneEvent``
  with the tier, removed-message delta, and freed-token delta.
- When the transcript is below threshold, no compaction event fires and
  the transcript is left untouched.
- The orchestrator's same-turn cooldown is respected: a turn that
  already compacted does not compact again on its own re-entry.
- A ``compactor=None`` config behaves identically to the slice-D
  orchestrator (no compaction work, no event).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dream.contracts.provider import ProviderCapabilities
from dream.engine._events import CompactionDoneEvent
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.engine._outcomes import SessionOutcome, TurnOutcome
from dream.engine._records import SessionEnd, TurnRecord
from dream.engine._session import SessionConfig, run_session
from dream.planner._artefacts import LedgerStep, PlannerLedger
from dream.services.compact._carryover_state import CarryoverMetadata
from dream.services.compact._orchestrator import AutoCompactState
from dream.services.compact._summariser import make_deterministic_summariser
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _heavy_user(text: str, *, kb: int = 8) -> ConversationMessage:
    """A user message padded with droppable tool results, so microcompact has
    something to free. ``ToolResultBlock`` is what the microcompact tier
    targets — pad with one bash result big enough to dominate the estimate.
    """
    payload = "x" * (kb * 1024)
    return ConversationMessage(
        role="user",
        content=[
            ToolResultBlock(
                tool_use_id=f"tu_{text}",
                content=payload,
                is_error=False,
            ),
            TextBlock(text=text),
        ],
    )


def _ticking_clock():
    base = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
    counter = [0]

    def now() -> datetime:
        t = base + timedelta(seconds=counter[0])
        counter[0] += 1
        return t

    return now


def _config(
    streamer: FakeStreamer,
    *,
    compactor: AutoCompactState | None,
    capabilities: ProviderCapabilities | None = None,
    threshold: float = 0.7,
    summariser=None,
    carryover: CarryoverMetadata | None = None,
    working_dir=None,
    hygiene_threshold: float | None = 0.85,
) -> SessionConfig:
    return SessionConfig(
        client=streamer,
        tools=FakeDispatcher(),
        max_turns=4,
        session_id="s_e",
        now=_ticking_clock(),
        compactor=compactor,
        compaction_capabilities=capabilities,
        compaction_threshold=threshold,
        compaction_summariser=summariser,
        carryover_metadata=carryover,
        working_dir=working_dir,
        compaction_hygiene_threshold=hygiene_threshold,
    )


async def _drain(config: SessionConfig, msgs: list[ConversationMessage]) -> list:
    out = []
    async for ev in run_session(config, msgs):
        out.append(ev)
    return out


# --- fields exist (RED if SessionConfig lacks them) --------------------------


def test_session_config_has_compaction_fields() -> None:
    """``SessionConfig`` exposes ``compactor`` + threshold + capabilities."""
    streamer = FakeStreamer([FakeTurn(text_chunks=["hi"])])
    cfg = SessionConfig(client=streamer, tools=FakeDispatcher())
    assert cfg.compactor is None
    assert cfg.compaction_threshold == pytest.approx(0.7)
    assert cfg.compaction_capabilities is None
    assert cfg.compaction_preserve_recent >= 1


# --- behaviour: no compactor = no event --------------------------------------


@pytest.mark.asyncio
async def test_no_compactor_no_compaction_event() -> None:
    streamer = FakeStreamer([FakeTurn(text_chunks=["ok"])])
    cfg = _config(streamer, compactor=None)
    events = await _drain(cfg, [_user("hi")])
    assert not any(isinstance(e, CompactionDoneEvent) for e in events)


# --- behaviour: below-threshold = no event ----------------------------------


@pytest.mark.asyncio
async def test_below_threshold_no_compaction() -> None:
    streamer = FakeStreamer([FakeTurn(text_chunks=["ok"])])
    caps = ProviderCapabilities(max_context_tokens=1_000_000)
    cfg = _config(streamer, compactor=AutoCompactState(), capabilities=caps)
    events = await _drain(cfg, [_user("hi")])
    assert not any(isinstance(e, CompactionDoneEvent) for e in events)


# --- behaviour: above-threshold = exactly one event -------------------------


@pytest.mark.asyncio
async def test_above_threshold_emits_one_compaction_event() -> None:
    """With a tiny window + heavy tool-result transcript, the orchestrator
    microcompacts before the turn runs and ``run_session`` yields exactly
    one ``CompactionDoneEvent``.
    """
    streamer = FakeStreamer([FakeTurn(text_chunks=["ack"])])
    # 8 KB of tool-result content per padded message; an 8K-token window
    # is well below the 0.7 utilisation threshold after a few entries.
    caps = ProviderCapabilities(max_context_tokens=8_000)
    state = AutoCompactState()
    cfg = _config(streamer, compactor=state, capabilities=caps, threshold=0.5)

    # Resume with several heavy tool-result messages, each preceded by the
    # matching assistant tool_use, so the transcript is well-formed and
    # ``has_pending_continuation`` is False (last message is the user
    # follow-up below). Utilisation lands well above the 0.5 threshold.
    resume: list[ConversationMessage] = []
    for i in range(4):
        resume.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu_r{i}", name="bash", input={"cmd": "x"})],
            )
        )
        resume.append(_heavy_user(f"r{i}", kb=4))
    # End on an assistant text so the loop will consume the new user message
    # rather than treating the transcript as a pending tool-result round.
    resume.append(ConversationMessage(role="assistant", content=[TextBlock(text="prior summary")]))

    out = []
    async for ev in run_session(cfg, [_user("next")], resume_messages=resume):
        out.append(ev)

    compacted = [e for e in out if isinstance(e, CompactionDoneEvent)]
    assert len(compacted) == 1, f"expected one CompactionDoneEvent, got {len(compacted)}"
    ev = compacted[0]
    assert ev.tier in ("microcompact", "full")
    assert ev.removed_messages >= 0
    assert ev.freed_tokens >= 0


# --- behaviour: cooldown -----------------------------------------------------


@pytest.mark.asyncio
async def test_same_turn_cooldown_prevents_double_compaction() -> None:
    """If compaction fired this turn, ``begin_turn`` on the *next* turn
    resets the cooldown — but within a single turn no second auto-compaction
    happens. With only one user message we end after one turn; no second
    event should appear.
    """
    streamer = FakeStreamer([FakeTurn(text_chunks=["ack"])])
    caps = ProviderCapabilities(max_context_tokens=8_000)
    state = AutoCompactState()
    cfg = _config(streamer, compactor=state, capabilities=caps, threshold=0.5)
    resume: list[ConversationMessage] = []
    for i in range(3):
        resume.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu_c{i}", name="bash", input={"cmd": "x"})],
            )
        )
        resume.append(_heavy_user(f"c{i}", kb=4))
    resume.append(ConversationMessage(role="assistant", content=[TextBlock(text="prior summary")]))
    out = []
    async for ev in run_session(cfg, [_user("next")], resume_messages=resume):
        out.append(ev)
    compacted = [e for e in out if isinstance(e, CompactionDoneEvent)]
    # At most one event (zero if the resume sanitization brought it under
    # threshold; one if the heavy tool-results dominated).
    assert len(compacted) <= 1


@pytest.mark.asyncio
async def test_wired_summariser_hits_full_tier() -> None:
    """When microcompact alone cannot reach threshold, the session path
    escalates to tier=full if a summariser is wired (P0 #4 Wave A).
    """
    streamer = FakeStreamer([FakeTurn(text_chunks=["ack"])])
    caps = ProviderCapabilities(max_context_tokens=8_000)
    state = AutoCompactState()
    carryover = CarryoverMetadata()
    summariser = make_deterministic_summariser(carryover)
    cfg = _config(
        streamer,
        compactor=state,
        capabilities=caps,
        threshold=0.5,
        summariser=summariser,
        carryover=carryover,
    )

    resume: list[ConversationMessage] = []
    for i in range(8):
        resume.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu_f{i}", name="read_file", input={"path": "x"})],
            )
        )
        resume.append(_heavy_user(f"f{i}", kb=4))
    resume.append(ConversationMessage(role="assistant", content=[TextBlock(text="prior summary")]))

    out = []
    async for ev in run_session(cfg, [_user("next")], resume_messages=resume):
        out.append(ev)

    compacted = [e for e in out if isinstance(e, CompactionDoneEvent)]
    assert len(compacted) == 1
    assert compacted[0].tier == "full"
    assert carryover.last_compacted_transcript is not None
    assert any(
        "[Compaction summary" in msg.text
        for msg in carryover.last_compacted_transcript
    )


@pytest.mark.asyncio
async def test_context_pressure_skips_act_loop_when_still_over_threshold() -> None:
    """Wave C: after compaction, lingering pressure ends turn without model call."""
    streamer = FakeStreamer([FakeTurn(text_chunks=["should-not-run"])])
    caps = ProviderCapabilities(max_context_tokens=8_000)
    state = AutoCompactState()
    cfg = _config(streamer, compactor=state, capabilities=caps, threshold=0.5)

    resume: list[ConversationMessage] = [ConversationMessage(role="user", content=[TextBlock(text="kick off")])]
    for i in range(12):
        resume.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu_p{i}", name="read_file", input={"path": "x"})],
            )
        )
        resume.append(
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=f"tu_p{i}",
                        content="x" * 20_000,
                        is_error=False,
                    )
                ],
            )
        )

    out = []
    async for ev in run_session(cfg, [_user("next")], resume_messages=resume):
        out.append(ev)

    assert streamer.calls == []
    records = [e for e in out if isinstance(e, TurnRecord)]
    assert any(r.outcome == TurnOutcome.CONTEXT_PRESSURE for r in records)
    compacted = [e for e in out if isinstance(e, CompactionDoneEvent)]
    assert len(compacted) == 1
    session_ends = [e for e in out if isinstance(e, SessionEnd)]
    assert session_ends[-1].outcome == SessionOutcome.DONE_WITH_WARNINGS
    assert "context-pressure" in (session_ends[-1].reason or "")


@pytest.mark.asyncio
async def test_hygiene_compact_runs_after_successful_turn() -> None:
    """Wave C: end-of-turn hygiene fence compacts when utilisation crosses threshold."""
    streamer = FakeStreamer([FakeTurn(text_chunks=["done"])])
    caps = ProviderCapabilities(max_context_tokens=8_000)
    state = AutoCompactState()
    cfg = _config(
        streamer,
        compactor=state,
        capabilities=caps,
        threshold=0.95,
        hygiene_threshold=0.4,
    )

    resume: list[ConversationMessage] = []
    for i in range(4):
        resume.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu_h{i}", name="bash", input={"cmd": "x"})],
            )
        )
        resume.append(_heavy_user(f"h{i}", kb=4))
    resume.append(ConversationMessage(role="assistant", content=[TextBlock(text="prior summary")]))

    out = []
    async for ev in run_session(cfg, [_user("next")], resume_messages=resume):
        out.append(ev)

    compacted = [e for e in out if isinstance(e, CompactionDoneEvent)]
    assert len(compacted) == 1
    records = [e for e in out if isinstance(e, TurnRecord)]
    assert records[0].outcome == TurnOutcome.COMPLETE
    assert streamer.calls, "model should run before hygiene compact"


@pytest.mark.asyncio
async def test_maybe_compact_refreshes_carryover_from_exec_plan(tmp_path) -> None:
    """Wave C: carryover picks up exec-plan ledger before compaction."""
    active = tmp_path / "docs" / "exec-plans" / "active"
    active.mkdir(parents=True)
    ledger = PlannerLedger(
        task_id="task-1",
        intent="ship",
        created_at=1.0,
        steps=(
            LedgerStep(id="s1", description="active", status="in_progress"),
        ),
    )
    ledger.save(active / "task-1.json")

    streamer = FakeStreamer([FakeTurn(text_chunks=["ack"])])
    caps = ProviderCapabilities(max_context_tokens=8_000)
    state = AutoCompactState()
    carryover = CarryoverMetadata()
    cfg = _config(
        streamer,
        compactor=state,
        capabilities=caps,
        threshold=0.5,
        carryover=carryover,
        working_dir=tmp_path,
    )

    resume: list[ConversationMessage] = []
    for i in range(4):
        resume.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu_c{i}", name="bash", input={"cmd": "x"})],
            )
        )
        resume.append(_heavy_user(f"c{i}", kb=4))
    resume.append(ConversationMessage(role="assistant", content=[TextBlock(text="prior summary")]))

    async for _ in run_session(cfg, [_user("next")], resume_messages=resume):
        pass

    assert carryover.exec_plan_filename == "task-1.json"
    assert carryover.exec_plan_current_step == "s1"
