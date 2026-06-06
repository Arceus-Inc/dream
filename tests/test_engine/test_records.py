"""Spec 03 stage 3a — ``TurnRecord`` + ``SessionEnd`` + jsonl serialization.

Each completed turn writes exactly one ``TurnRecord`` to the session jsonl
(spec 03 acceptance #3 + #8). Every exit path writes exactly one
``SessionEnd`` (#2). The pair is the audit trail recovery, observability,
and `progress.md` updates all read back.

This module pins the record shapes and round-trip serialization. Disk
layout (where the file lives, atomic-append semantics) is owned by
``_session.py`` and tested there.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._records import (
    SessionEnd,
    TurnRecord,
    from_jsonl_line,
    to_jsonl_line,
)

# --- TurnRecord shape --------------------------------------------------------


def _t(seconds: int) -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def test_turn_record_carries_all_required_fields() -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=("read", "bash"),
        verification_result="pass",
        outcome="complete",
        usage=UsageSnapshot(input_tokens=10, output_tokens=5),
        notes="picked up exec-plan step 3",
    )
    assert rec.turn_number == 1
    assert rec.started_at == _t(0)
    assert rec.ended_at == _t(5)
    assert rec.tools_called == ("read", "bash")
    assert rec.verification_result == "pass"
    assert rec.outcome == "complete"
    assert rec.usage == UsageSnapshot(input_tokens=10, output_tokens=5)
    assert rec.notes == "picked up exec-plan step 3"


def test_turn_record_is_frozen() -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=(),
        verification_result="skipped",
        outcome="complete",
        usage=UsageSnapshot(),
    )
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(rec, "turn_number", 99)


def test_turn_record_notes_defaults_to_empty_string() -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=(),
        verification_result="skipped",
        outcome="complete",
        usage=UsageSnapshot(),
    )
    assert rec.notes == ""


@pytest.mark.parametrize("outcome", ["complete", "timeout", "aborted"])
def test_turn_record_accepts_all_outcomes(outcome: str) -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=(),
        verification_result="skipped",
        outcome=outcome,  # type: ignore[arg-type]
        usage=UsageSnapshot(),
    )
    assert rec.outcome == outcome


@pytest.mark.parametrize("verification_result", ["pass", "fail", "skipped"])
def test_turn_record_accepts_all_verification_results(verification_result: str) -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=(),
        verification_result=verification_result,  # type: ignore[arg-type]
        outcome="complete",
        usage=UsageSnapshot(),
    )
    assert rec.verification_result == verification_result


# --- SessionEnd shape --------------------------------------------------------


def test_session_end_carries_all_required_fields() -> None:
    rec = SessionEnd(
        session_id="s_test",
        started_at=_t(0),
        ended_at=_t(60),
        turns=4,
        total_usage=UsageSnapshot(input_tokens=100, output_tokens=50),
        outcome="done",
    )
    assert rec.session_id == "s_test"
    assert rec.started_at == _t(0)
    assert rec.ended_at == _t(60)
    assert rec.turns == 4
    assert rec.total_usage == UsageSnapshot(input_tokens=100, output_tokens=50)
    assert rec.outcome == "done"
    assert rec.reason is None


def test_session_end_carries_reason_for_aborts() -> None:
    rec = SessionEnd(
        session_id="s_test",
        started_at=_t(0),
        ended_at=_t(60),
        turns=2,
        total_usage=UsageSnapshot(),
        outcome="aborted",
        reason="coma",
    )
    assert rec.outcome == "aborted"
    assert rec.reason == "coma"


def test_session_end_is_frozen() -> None:
    rec = SessionEnd(
        session_id="s_test",
        started_at=_t(0),
        ended_at=_t(60),
        turns=0,
        total_usage=UsageSnapshot(),
        outcome="done",
    )
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(rec, "outcome", "aborted")


@pytest.mark.parametrize("outcome", ["done", "done-with-warnings", "aborted"])
def test_session_end_accepts_all_outcomes(outcome: str) -> None:
    rec = SessionEnd(
        session_id="s_test",
        started_at=_t(0),
        ended_at=_t(60),
        turns=0,
        total_usage=UsageSnapshot(),
        outcome=outcome,  # type: ignore[arg-type]
    )
    assert rec.outcome == outcome


# --- jsonl serialization ----------------------------------------------------


def test_turn_record_jsonl_line_is_single_line_json() -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=("read",),
        verification_result="pass",
        outcome="complete",
        usage=UsageSnapshot(input_tokens=10, output_tokens=5),
        notes="ok",
    )
    line = to_jsonl_line(rec)
    assert "\n" not in line
    parsed = json.loads(line)
    # Discriminator so consumers can branch on read.
    assert parsed["kind"] == "turn"
    assert parsed["turn_number"] == 1
    assert parsed["outcome"] == "complete"


def test_session_end_jsonl_line_carries_kind_discriminator() -> None:
    rec = SessionEnd(
        session_id="s_test",
        started_at=_t(0),
        ended_at=_t(60),
        turns=0,
        total_usage=UsageSnapshot(),
        outcome="done",
    )
    line = to_jsonl_line(rec)
    parsed = json.loads(line)
    assert parsed["kind"] == "session_end"


def test_turn_record_round_trip_through_jsonl() -> None:
    original = TurnRecord(
        turn_number=3,
        started_at=_t(10),
        ended_at=_t(15),
        tools_called=("read", "bash"),
        verification_result="fail",
        outcome="timeout",
        usage=UsageSnapshot(
            input_tokens=11, output_tokens=22, cache_read_tokens=3, cache_write_tokens=4
        ),
        notes="model went silent",
    )
    restored = from_jsonl_line(to_jsonl_line(original))
    assert restored == original


def test_session_end_round_trip_through_jsonl() -> None:
    original = SessionEnd(
        session_id="s_xyz",
        started_at=_t(0),
        ended_at=_t(300),
        turns=12,
        total_usage=UsageSnapshot(input_tokens=999, output_tokens=111),
        outcome="done-with-warnings",
        reason="reviewer-max-rounds",
    )
    restored = from_jsonl_line(to_jsonl_line(original))
    assert restored == original


def test_from_jsonl_line_returns_right_type_for_each_kind() -> None:
    turn = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=(),
        verification_result="skipped",
        outcome="complete",
        usage=UsageSnapshot(),
    )
    end = SessionEnd(
        session_id="s",
        started_at=_t(0),
        ended_at=_t(5),
        turns=1,
        total_usage=UsageSnapshot(),
        outcome="done",
    )
    assert isinstance(from_jsonl_line(to_jsonl_line(turn)), TurnRecord)
    assert isinstance(from_jsonl_line(to_jsonl_line(end)), SessionEnd)


def test_from_jsonl_line_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        from_jsonl_line(json.dumps({"kind": "garbage"}))


def test_to_jsonl_line_preserves_datetime_as_iso_string() -> None:
    rec = TurnRecord(
        turn_number=1,
        started_at=_t(0),
        ended_at=_t(5),
        tools_called=(),
        verification_result="skipped",
        outcome="complete",
        usage=UsageSnapshot(),
    )
    parsed = json.loads(to_jsonl_line(rec))
    # ISO format so external tooling (jq, log viewers) can parse the timeline.
    assert parsed["started_at"].startswith("2026-06-06T12:00:00")
    assert parsed["ended_at"].startswith("2026-06-06T12:00:05")


def test_from_jsonl_line_raises_valueerror_on_truncated_turn_record() -> None:
    """A truncated/crash-torn line missing required fields raises a controlled
    ``ValueError`` (a reader can skip it), not a bare ``KeyError``."""
    # Valid 'turn' discriminator + tools_called list, but every other field gone.
    partial = json.dumps({"kind": "turn", "tools_called": []})
    with pytest.raises(ValueError):
        from_jsonl_line(partial)


def test_from_jsonl_line_raises_valueerror_on_bad_usage_shape() -> None:
    """A wrong-typed nested field surfaces as ValueError, not TypeError."""
    line = to_jsonl_line(
        TurnRecord(
            turn_number=1,
            started_at=_t(0),
            ended_at=_t(1),
            tools_called=(),
            verification_result="skipped",
            outcome="complete",
            usage=UsageSnapshot(),
        )
    )
    corrupted = json.loads(line)
    corrupted["usage"] = {"not_a_usage_field": 1}
    with pytest.raises(ValueError):
        from_jsonl_line(json.dumps(corrupted))
