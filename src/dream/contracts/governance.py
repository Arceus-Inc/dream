"""The governance seam — the executive control plane a CEO employee's tools act on.

The mirror image of :mod:`dream.contracts.strategy`: where the strategy seam lets **horizon** write
intake into **chorus**, the governance seam lets a **chorus** employee (the CEO) read and steer
**horizon's** direction — inspect the decision/goal tree, adjudicate proposals, re-prioritise, archive.

Chorus's CEO capability tools bind to :class:`GovernancePort`; horizon supplies the concrete adapter at a
consumer's composition root (the one place allowed to know both). Neither side imports the other — they
meet only here, exactly like the strategy seam. The DTOs are plain read shapes (no I/O), so the contract
stays dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GovGoal:
    """A goal as the CEO sees it — the rich view plus its derived coarse priority."""

    goal_id: str
    title: str
    score: float = 0.0
    priority: str = "low"  # high | medium | low (derived from score)
    health: str = "unknown"  # on_track | drifting | blocked | unknown
    status: str = "active"
    task_id: str | None = None
    metric: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class GovDecision:
    """A decision with its current goals."""

    decision_id: str
    statement: str
    status: str = "active"
    goals: tuple[GovGoal, ...] = ()


@dataclass(frozen=True)
class GovProposal:
    """A funnel proposal awaiting a human/executive decision."""

    proposal_id: str
    statement: str
    status: str = "proposed"
    confidence: float | None = None
    evidence: int = 0  # count of evidence sources behind the proposal


@dataclass(frozen=True)
class GovernanceView:
    """The whole bounded snapshot the CEO reads: decisions + goals + open proposals."""

    decisions: tuple[GovDecision, ...] = field(default_factory=tuple)
    proposals: tuple[GovProposal, ...] = field(default_factory=tuple)


@runtime_checkable
class GovernancePort(Protocol):
    """The executive control plane a CEO employee steers — read the tree, adjudicate, re-prioritise.

    Horizon supplies the concrete adapter; a chorus CEO tool depends only on this shape. Every write is
    a real strategy-layer write (approve seeds a decision; reject closes a proposal; set_priority /
    archive re-aim a goal) — the executive's authority, exercised through the one governed seam.
    """

    def read_direction(self) -> GovernanceView:
        """The current direction: every decision with its goals, plus the open proposals."""
        ...

    def approve_proposal(self, proposal_id: str, *, by: str) -> str:
        """Approve a proposal -> seed a live decision + its goals; returns the new decision id."""
        ...

    def reject_proposal(self, proposal_id: str, *, by: str, reason: str = "") -> None:
        """Close a proposal without promoting it — records who / why."""
        ...

    def set_priority(self, goal_id: str, priority: str) -> str:
        """Override a goal's priority to high | medium | low; returns the priority set."""
        ...

    def archive_goal(self, goal_id: str) -> None:
        """Retire a goal from the active direction — it stops being steered."""
        ...


__all__ = [
    "GovDecision",
    "GovGoal",
    "GovProposal",
    "GovernancePort",
    "GovernanceView",
]
