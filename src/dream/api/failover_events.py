"""Typed observability events for substrate failover (Spec 02 sections 12-16).

Replaces untyped ``dict[str, Any]`` callbacks so callers cannot invent
stringly event shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class FailoverReason(Enum):
    """Why the policy advanced to the next substrate."""

    POOL_EXHAUSTED = "pool_exhausted"
    AUTH = "auth"
    TRANSIENT_EXHAUSTED = "transient_exhausted"
    COMA = "coma"
    BILLING = "billing"
    MODEL_NOT_FOUND = "model_not_found"


@dataclass(frozen=True)
class SubstrateFailoverEvent:
    from_substrate: str
    to_substrate: str
    reason: FailoverReason


@dataclass(frozen=True)
class SubstrateHealthRecoveredEvent:
    substrate: str


@dataclass(frozen=True)
class SubstrateHealthDegradedEvent:
    substrate: str


@dataclass(frozen=True)
class RecoveryAttemptEvent:
    """Audit: one classified recovery decision on the live streamer path."""

    substrate: str
    credential_label: str
    kind: str
    action: str


FailoverEvent = (
    SubstrateFailoverEvent
    | SubstrateHealthRecoveredEvent
    | SubstrateHealthDegradedEvent
    | RecoveryAttemptEvent
)

EventCallback = Callable[[FailoverEvent], None]


__all__ = [
    "EventCallback",
    "FailoverEvent",
    "FailoverReason",
    "RecoveryAttemptEvent",
    "SubstrateFailoverEvent",
    "SubstrateHealthDegradedEvent",
    "SubstrateHealthRecoveredEvent",
]
