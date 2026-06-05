"""Session and turn FSM transition tables (Spec 03 stage 3a).

The session and turn lifecycles are explicit state machines. Every legal
edge is enumerated here; ``is_valid_*_transition`` returns ``False`` for
anything else. The orchestrator in ``_session.py`` consults these tables
before firing a transition so an illegal walk is impossible by construction
rather than by convention.
"""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    STARTING = "starting"
    ORIENTING = "orienting"
    WORKING = "working"
    SEALING = "sealing"
    DONE = "done"
    ABORTED = "aborted"


class TurnState(StrEnum):
    READ = "read"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    RECORD = "record"


_SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.STARTING: frozenset({SessionState.ORIENTING, SessionState.ABORTED}),
    SessionState.ORIENTING: frozenset({SessionState.WORKING, SessionState.ABORTED}),
    # ``working → working`` is the per-turn back-edge; ``sealing`` is the only forward exit.
    SessionState.WORKING: frozenset(
        {SessionState.WORKING, SessionState.SEALING, SessionState.ABORTED}
    ),
    SessionState.SEALING: frozenset({SessionState.DONE, SessionState.ABORTED}),
    SessionState.DONE: frozenset(),
    SessionState.ABORTED: frozenset(),
}


_TURN_TRANSITIONS: dict[TurnState, frozenset[TurnState]] = {
    TurnState.READ: frozenset({TurnState.PLAN}),
    TurnState.PLAN: frozenset({TurnState.ACT}),
    TurnState.ACT: frozenset({TurnState.VERIFY}),
    TurnState.VERIFY: frozenset({TurnState.RECORD}),
    TurnState.RECORD: frozenset(),
}


def is_valid_session_transition(src: SessionState, dst: SessionState) -> bool:
    return dst in _SESSION_TRANSITIONS.get(src, frozenset())


def is_valid_turn_transition(src: TurnState, dst: TurnState) -> bool:
    return dst in _TURN_TRANSITIONS.get(src, frozenset())


__all__ = [
    "SessionState",
    "TurnState",
    "is_valid_session_transition",
    "is_valid_turn_transition",
]
