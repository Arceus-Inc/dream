"""Delegated-work and observed-capacity contracts shared across sibling repositories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from dream.contracts.strategy import Priority


@dataclass(frozen=True)
class StaffingRequirement:
    """One profession required to execute delegated work."""

    profession: str
    count: int = 1
    coverage: Literal["direct", "subtree"] = "direct"
    outcome_area: str | None = None

    def __post_init__(self) -> None:
        """Validate once at the contract so every consumer inherits the invariants."""
        if not self.profession.strip():
            raise ValueError("StaffingRequirement.profession must be non-empty")
        if self.count < 1:
            raise ValueError(f"StaffingRequirement.count must be >= 1, got {self.count}")


@dataclass(frozen=True)
class DelegatedWorkRequest:
    """Team-shaped work requested by strategy without naming a task topology."""

    intent: str
    goal_id: str
    priority: Priority = "medium"
    requirements: tuple[StaffingRequirement, ...] = ()
    lead_professions: tuple[str, ...] = ()
    preferred_lead: str | None = None
    max_team_size: int | None = None
    spend_limit_cents: int | None = None
    origin_fingerprint: str = ""

    def __post_init__(self) -> None:
        """Validate once at the contract so every consumer inherits the invariants."""
        if not self.intent.strip():
            raise ValueError("DelegatedWorkRequest.intent must be non-empty")
        if not self.goal_id.strip():
            raise ValueError("DelegatedWorkRequest.goal_id must be non-empty")
        if self.max_team_size is not None and self.max_team_size < 1:
            raise ValueError(
                f"DelegatedWorkRequest.max_team_size must be >= 1, got {self.max_team_size}"
            )
        if self.spend_limit_cents is not None and self.spend_limit_cents < 0:
            raise ValueError(
                f"DelegatedWorkRequest.spend_limit_cents must be >= 0, got {self.spend_limit_cents}"
            )


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

    def submit_delegated(self, request: DelegatedWorkRequest) -> DelegatedWorkRef | StaffingBlocked:
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
