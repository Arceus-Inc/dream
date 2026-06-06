"""Spec 06.5 slice 2 — per-agent persistent ``HeartbeatState``.

The skip-streak counter is the load-bearing piece of the anti-coma
guard: it lives on disk so a process restart can't reset a depressed
agent's streak to zero. All access goes through ``read_state`` /
``write_state``, which take the same lock the orchestrator does so a
second wake can't tear a read-modify-write.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.wake._state import (
    HeartbeatState,
    read_state,
    state_path_for,
    write_state,
)


def _t() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


# --- shape ------------------------------------------------------------------


def test_default_state_is_zero_streak_no_last_decision() -> None:
    s = HeartbeatState()
    assert s.skip_streak == 0
    assert s.last_decided_at is None


def test_state_is_frozen() -> None:
    s = HeartbeatState()
    with pytest.raises((AttributeError, TypeError)):
        setattr(s, "skip_streak", 5)


def test_state_with_skip_streak_keeps_other_fields() -> None:
    """``with_skip_streak`` is the explicit copy-with helper the
    orchestrator uses to bump the counter without monkey-patching a
    frozen dataclass."""
    s = HeartbeatState(skip_streak=2, last_decided_at=_t())
    bumped = s.with_skip_streak(3)
    assert bumped.skip_streak == 3
    assert bumped.last_decided_at == _t()


def test_state_with_last_decided_at() -> None:
    s = HeartbeatState(skip_streak=2)
    later = datetime(2026, 6, 7, 0, 0, 0, tzinfo=UTC)
    moved = s.with_last_decided_at(later)
    assert moved.last_decided_at == later
    assert moved.skip_streak == 2


# --- path resolution --------------------------------------------------------


def test_state_path_for_uses_agent_id(tmp_path: Path) -> None:
    p = state_path_for(tmp_path, agent_id="curator")
    assert p.name == "heartbeat-curator.skip-streak.json"
    assert p.parent == tmp_path


def test_state_path_for_rejects_path_separators(tmp_path: Path) -> None:
    """Agent IDs are filename fragments — slashes would escape the
    coordination dir."""
    with pytest.raises(ValueError, match="agent_id"):
        state_path_for(tmp_path, agent_id="curator/evil")


def test_state_path_for_rejects_empty_agent_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="agent_id"):
        state_path_for(tmp_path, agent_id="")


# --- read / write -----------------------------------------------------------


def test_read_state_returns_default_when_missing(tmp_path: Path) -> None:
    """Fresh agent — no file, no error, just defaults."""
    s = read_state(state_path_for(tmp_path, agent_id="curator"))
    assert s == HeartbeatState()


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = state_path_for(tmp_path, agent_id="curator")
    write_state(path, HeartbeatState(skip_streak=3, last_decided_at=_t()))
    assert read_state(path) == HeartbeatState(skip_streak=3, last_decided_at=_t())


def test_write_creates_parent_dir(tmp_path: Path) -> None:
    """Coordination dir is allowed to not exist yet."""
    path = tmp_path / "coordination" / "heartbeat-curator.skip-streak.json"
    assert not path.parent.exists()
    write_state(path, HeartbeatState(skip_streak=1))
    assert path.parent.exists()
    assert path.exists()


def test_read_state_recovers_from_torn_file(tmp_path: Path) -> None:
    """A torn / non-JSON file falls back to default state (and is overwritten
    on next write). Coma-on-startup is worse than a missed skip count."""
    path = state_path_for(tmp_path, agent_id="curator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    s = read_state(path)
    assert s == HeartbeatState()


def test_read_state_recovers_from_missing_fields(tmp_path: Path) -> None:
    """Forward-compat: an old shape (missing ``last_decided_at``) reads cleanly."""
    path = state_path_for(tmp_path, agent_id="curator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skip_streak": 4}), encoding="utf-8")
    s = read_state(path)
    assert s.skip_streak == 4
    assert s.last_decided_at is None


def test_read_state_ignores_unknown_fields(tmp_path: Path) -> None:
    """Forward-compat: a future field we don't know about doesn't break read."""
    path = state_path_for(tmp_path, agent_id="curator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"skip_streak": 1, "next_field_we_dont_have_yet": "x"}),
        encoding="utf-8",
    )
    s = read_state(path)
    assert s.skip_streak == 1


def test_write_state_is_idempotent(tmp_path: Path) -> None:
    path = state_path_for(tmp_path, agent_id="curator")
    s = HeartbeatState(skip_streak=2)
    write_state(path, s)
    write_state(path, s)
    assert read_state(path) == s


def test_write_state_overwrites(tmp_path: Path) -> None:
    path = state_path_for(tmp_path, agent_id="curator")
    write_state(path, HeartbeatState(skip_streak=2))
    write_state(path, HeartbeatState(skip_streak=5))
    assert read_state(path).skip_streak == 5
