"""Delegated-work and observed-capacity contracts shared across sibling repositories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dream.contracts.strategy import Priority


@dataclass(frozen=True)
class StaffingRequirement:
    """One profession required to execute delegated work."""

    profession: str
    count: int = 1


@dataclass(frozen=True)
class DelegatedWorkRequest:
    """Team-shaped work requested by strategy without naming a task topology."""

    intent: str
    goal_id: str
    priority: Priority = "medium"
    requirements: tuple[StaffingRequirement, ...] = ()
    preferred_lead: str | None = None
    max_team_size: int | None = None
    spend_limit_cents: int | None = None
    origin_fingerprint: str = ""


@dataclass(frozen=True)
class DelegatedWorkRef:
    """Durable Chorus identities created for one delegated request."""

    root_task_id: str
    team_id: str
    lead_id: str


@dataclass(frozen=True)
class StaffingBlocked:
    """A team-shaped request could not be matched to an authorized lead."""

    goal_id: str
    reason: str
    kind: str = "staffing_blocked"


@runtime_checkable
class DelegatedIntakePort(Protocol):
    """Create one policy-selected delegated root from a strategy request."""

    def submit_delegated(
        self, request: DelegatedWorkRequest
    ) -> DelegatedWorkRef | StaffingBlocked:
        """Submit delegated work idempotently and return its durable identities."""
        ...


@dataclass(frozen=True)
class ProfessionCapacity:
    """Observed execution and budget facts for one profession."""

    profession: str
    eligible: int
    running: int
    assigned_nonterminal: int
    queued_wakes: int
    budget_blocked: int
    budget_headroom_cents: int | None


@runtime_checkable
class CapacityPort(Protocol):
    """Read aggregate capacity without exposing employee scheduling controls."""

    def snapshot(self) -> tuple[ProfessionCapacity, ...]:
        """Return the current aggregate capacity snapshot."""
        ...


__all__ = [
    "CapacityPort",
    "DelegatedIntakePort",
    "DelegatedWorkRef",
    "DelegatedWorkRequest",
    "ProfessionCapacity",
    "StaffingBlocked",
    "StaffingRequirement",
]