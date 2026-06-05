"""Spec 03 stage 3a — ``SessionState`` / ``TurnState`` FSM transition tables.

The session and turn lifecycles are explicit state machines. Every legal
transition is pinned here; everything else is invalid. This is what makes
the rituals in `_session.py` provably bounded — the orchestrator can't
silently skip orientation, can't re-orient mid-session, can't loop a turn
back to ``read``, can't escape ``done``/``aborted``.

Acceptance covered: spec 03 #1 (session order, no skipping, no re-orient).
"""

from __future__ import annotations

import pytest

from dream.engine._fsm import (
    SessionState,
    TurnState,
    is_valid_session_transition,
    is_valid_turn_transition,
)

# --- SessionState enum -------------------------------------------------------


def test_session_state_has_exactly_six_members() -> None:
    """starting → orienting → working → sealing → done | aborted."""
    assert {s.value for s in SessionState} == {
        "starting",
        "orienting",
        "working",
        "sealing",
        "done",
        "aborted",
    }


def test_session_state_values_are_lowercase_strings() -> None:
    """Used directly in transition-event names like 'session.starting.to.orienting'."""
    for s in SessionState:
        assert s.value == s.value.lower()
        assert " " not in s.value


# --- session transitions: happy path -----------------------------------------


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (SessionState.STARTING, SessionState.ORIENTING),
        (SessionState.ORIENTING, SessionState.WORKING),
        (SessionState.WORKING, SessionState.WORKING),  # back-edge: one per completed turn
        (SessionState.WORKING, SessionState.SEALING),
        (SessionState.SEALING, SessionState.DONE),
    ],
)
def test_session_happy_path_transitions_allowed(
    src: SessionState, dst: SessionState
) -> None:
    assert is_valid_session_transition(src, dst) is True


# --- session transitions: abort reachable from active states -----------------


@pytest.mark.parametrize(
    "src",
    [
        SessionState.STARTING,
        SessionState.ORIENTING,
        SessionState.WORKING,
        SessionState.SEALING,
    ],
)
def test_session_abort_reachable_from_every_active_state(src: SessionState) -> None:
    """Spec 03 #2: session.end emitted on every exit path including abort."""
    assert is_valid_session_transition(src, SessionState.ABORTED) is True


# --- session transitions: forbidden ------------------------------------------


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        # Skipping orienting
        (SessionState.STARTING, SessionState.WORKING),
        (SessionState.STARTING, SessionState.SEALING),
        (SessionState.STARTING, SessionState.DONE),
        # Skipping working
        (SessionState.ORIENTING, SessionState.SEALING),
        (SessionState.ORIENTING, SessionState.DONE),
        # Skipping sealing
        (SessionState.WORKING, SessionState.DONE),
        # Re-orient (spec 03 #1: never re-enter orienting)
        (SessionState.WORKING, SessionState.ORIENTING),
        (SessionState.SEALING, SessionState.ORIENTING),
        # Re-enter working from sealing (sealing is one-way)
        (SessionState.SEALING, SessionState.WORKING),
        # Back-edges from terminal states
        (SessionState.DONE, SessionState.WORKING),
        (SessionState.DONE, SessionState.STARTING),
        (SessionState.ABORTED, SessionState.WORKING),
    ],
)
def test_session_forbidden_transitions_rejected(
    src: SessionState, dst: SessionState
) -> None:
    assert is_valid_session_transition(src, dst) is False


def test_session_terminal_states_have_no_outgoing_transitions() -> None:
    for dst in SessionState:
        assert is_valid_session_transition(SessionState.DONE, dst) is False
        assert is_valid_session_transition(SessionState.ABORTED, dst) is False


def test_session_state_cannot_transition_to_itself_except_working() -> None:
    """The only legal self-loop is ``working → working`` (one per completed turn)."""
    for s in SessionState:
        if s is SessionState.WORKING:
            assert is_valid_session_transition(s, s) is True
        else:
            assert is_valid_session_transition(s, s) is False


# --- TurnState enum ----------------------------------------------------------


def test_turn_state_has_exactly_five_members() -> None:
    """read → plan → act → verify → record."""
    assert {s.value for s in TurnState} == {"read", "plan", "act", "verify", "record"}


# --- turn transitions: happy path --------------------------------------------


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (TurnState.READ, TurnState.PLAN),
        (TurnState.PLAN, TurnState.ACT),
        (TurnState.ACT, TurnState.VERIFY),
        (TurnState.VERIFY, TurnState.RECORD),
    ],
)
def test_turn_happy_path_transitions_allowed(
    src: TurnState, dst: TurnState
) -> None:
    assert is_valid_turn_transition(src, dst) is True


# --- turn transitions: forbidden ---------------------------------------------


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        # Skipping ahead
        (TurnState.READ, TurnState.ACT),
        (TurnState.READ, TurnState.RECORD),
        (TurnState.PLAN, TurnState.VERIFY),
        (TurnState.ACT, TurnState.RECORD),
        # Back-edges
        (TurnState.PLAN, TurnState.READ),
        (TurnState.ACT, TurnState.PLAN),
        (TurnState.RECORD, TurnState.READ),
        (TurnState.RECORD, TurnState.ACT),
        # Self-loop
        (TurnState.ACT, TurnState.ACT),
        (TurnState.RECORD, TurnState.RECORD),
    ],
)
def test_turn_forbidden_transitions_rejected(
    src: TurnState, dst: TurnState
) -> None:
    assert is_valid_turn_transition(src, dst) is False


def test_turn_record_is_terminal_within_a_turn() -> None:
    """A turn ends at ``record``; the *session* decides whether to start a new turn."""
    for dst in TurnState:
        assert is_valid_turn_transition(TurnState.RECORD, dst) is False
