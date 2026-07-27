"""Session / turn outcome enums (Spec 03 records + internal session driver)."""

from __future__ import annotations

from enum import StrEnum


class VerificationResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class TurnOutcome(StrEnum):
    COMPLETE = "complete"
    TIMEOUT = "timeout"
    ABORTED = "aborted"
    CONTEXT_PRESSURE = "context-pressure"


class SessionOutcome(StrEnum):
    DONE = "done"
    DONE_WITH_WARNINGS = "done-with-warnings"
    ABORTED = "aborted"


class TurnEndKind(StrEnum):
    """How a single driven turn ended — before mapping to :class:`TurnOutcome`."""

    COMPLETE = "complete"
    TIMEOUT = "timeout"
    COMA = "coma"
    ERROR = "error"
    CONTEXT_PRESSURE = "context-pressure"


__all__ = [
    "SessionOutcome",
    "TurnEndKind",
    "TurnOutcome",
    "VerificationResult",
]
