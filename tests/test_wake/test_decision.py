"""Spec 06.5 slice 1 — ``HeartbeatDecision`` record + jsonl serialization.

The wake-cycle heartbeat (this spec) is a different abstraction from the
liveness/coma heartbeat (Spec 03 ``engine/_heartbeat.py``). One wake-cycle
turn produces exactly one ``HeartbeatDecision`` which gets appended to the
session jsonl as a ``kind: "heartbeat-decision"`` line so the audit trail
can replay why the agent chose to start (or skip) work.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from dream.wake import HeartbeatDecision
from dream.wake._decision import (
    from_jsonl_line,
    to_jsonl_line,
)


def _t() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


def test_decision_carries_all_required_fields() -> None:
    d = HeartbeatDecision(
        decided_at=_t(),
        action="run",
        tasks=("ship slice 1", "open PR"),
        reason="exec plan step 3 is unblocked",
    )
    assert d.decided_at == _t()
    assert d.action == "run"
    assert d.tasks == ("ship slice 1", "open PR")
    assert d.reason == "exec plan step 3 is unblocked"
    # Slice-1 defaults — populated by slice 2.
    assert d.wake_source is None
    assert d.forced is False
    assert d.outcome == "decided"


def test_decision_is_frozen() -> None:
    d = HeartbeatDecision(
        decided_at=_t(), action="skip", tasks=(), reason="nothing pending"
    )
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(d, "action", "run")


def test_decision_jsonl_roundtrip_skip() -> None:
    d = HeartbeatDecision(
        decided_at=_t(),
        action="skip",
        tasks=(),
        reason="nothing pending",
        wake_source="idle_timer",
        forced=False,
        outcome="decided",
    )
    line = to_jsonl_line(d)
    # Single line, parseable as JSON, carries the discriminator.
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["kind"] == "heartbeat-decision"
    # Roundtrip restores every field.
    back = from_jsonl_line(line)
    assert back == d


def test_decision_jsonl_roundtrip_run_with_tasks() -> None:
    d = HeartbeatDecision(
        decided_at=_t(),
        action="run",
        tasks=("a", "b", "c"),
        reason="three things to do",
        wake_source="cron",
        forced=False,
    )
    back = from_jsonl_line(to_jsonl_line(d))
    assert back == d
    assert back.tasks == ("a", "b", "c")  # tuple, not list


def test_decision_jsonl_records_missing_outcome() -> None:
    """The 'model produced no heartbeat tool call' outcome is jsonl-preservable."""
    d = HeartbeatDecision(
        decided_at=_t(),
        action="skip",
        tasks=(),
        reason="heartbeat_missing_decision",
        wake_source="cron",
        outcome="missing",
    )
    back = from_jsonl_line(to_jsonl_line(d))
    assert back.outcome == "missing"
    assert back == d


def test_decision_jsonl_records_forced_run() -> None:
    """Slice 2 anti-coma path forces a run; the flag must roundtrip."""
    d = HeartbeatDecision(
        decided_at=_t(),
        action="run",
        tasks=("forced wake",),
        reason="anti_coma_forced_run",
        wake_source="idle_timer",
        forced=True,
    )
    back = from_jsonl_line(to_jsonl_line(d))
    assert back.forced is True
    assert back == d


def test_from_jsonl_line_rejects_wrong_kind() -> None:
    bad = json.dumps({"kind": "turn", "turn_number": 0})
    with pytest.raises(ValueError, match="kind"):
        from_jsonl_line(bad)


def test_from_jsonl_line_rejects_malformed_record() -> None:
    bad = json.dumps({"kind": "heartbeat-decision"})  # missing required fields
    with pytest.raises(ValueError):
        from_jsonl_line(bad)
