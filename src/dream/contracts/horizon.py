"""Horizon seam — the cross-repo Protocols the strategy layer binds to (chorus spec 00 §5a / 10 §5).

``horizon`` decides *which* work exists, at *what* priority, and *whether the company is still aimed
right*. It must do so **without importing chorus** — so the two siblings meet here, at three typed
Protocols + a few plain data shapes, exactly like every other contract in this package.

- :class:`IntakePort`  — the one reserved intake door (open a depth=0 task; re-rank a task's priority).
- :class:`GoalStore`   — read/write the OKR/alignment tree (the ``goal`` rows chorus reads for alignment).
- :class:`OutcomeFeed` — subscribe to / replay *landed* outcomes (push-driven back-pressure).

Concrete implementations are **adapters over chorus** built by a consumer's composition root (which is
allowed to import both). ``horizon`` itself binds only to these shapes. The data classes are dream-owned
so ``horizon`` need never touch chorus's ``Goal`` / ``Event`` types.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# chorus's coarse scheduler priority (``TaskPriority`` values). The strategy layer keeps a rich numeric
# score internally and maps to one of these at the intake boundary.
Priority = str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class GoalNode:
    """A node in the OKR/alignment tree, as the strategy layer sees it.

    Mirrors the fields chorus reads from the ``goal`` row (identity/level/status/parent) plus the small
    strategy fields horizon steers with (score/health/metric/target). Strategy-only depth (evidence,
    rationale, decision log) lives in horizon's own store, keyed by ``id`` — not here.
    """

    id: str
    title: str
    level: str = "objective"  # company|product|objective|key_result|initiative|task_intent
    status: str = "active"  # proposed|active|paused|done|archived
    parent_id: str | None = None
    owner: str | None = None
    score: float = 0.0  # horizon's numeric priority score (mapped to Priority at submit)
    health: str = "unknown"  # on_track|drifting|blocked|unknown
    metric: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class OutcomeEvent:
    """A *landed* task outcome, reduced to what the strategy layer needs (outcomes, not prose).

    A composition-root adapter maps chorus's typed ``Event`` stream onto this shape, so horizon reacts to
    ``run.done`` / ``run.evaluated`` / ``task.status`` / ``recovery.escalated`` without importing chorus.
    """

    kind: str  # e.g. "run.done" | "run.evaluated" | "task.status" | "recovery.escalated"
    task_id: str | None = None
    goal_id: str | None = None
    status: str | None = None  # "done" | "failed" | "blocked" | ...
    passed: bool | None = None  # the DoD verdict, when the event carries one
    detail: str = ""


@runtime_checkable
class IntakePort(Protocol):
    """The one reserved intake door — horizon opens depth=0 tasks and re-ranks priority through it.

    ``submit`` must be **idempotent** on ``(origin_kind=horizon_intake, origin_fingerprint)`` so
    re-deriving the same opportunity is a no-op. ``set_priority`` is a pure data write to
    ``task.priority`` — never a call into the scheduler.
    """

    def submit(
        self,
        intent: str,
        *,
        assignee: str | None = None,
        priority: Priority = "medium",
        depends_on: Sequence[str] = (),
        goal_id: str | None = None,
        origin_fingerprint: str | None = None,
    ) -> str:
        """Create a depth=0 intake task linked to ``goal_id``; return its task id."""
        ...

    def set_priority(self, task_id: str, priority: Priority) -> None:
        """Re-rank one task by writing ``task.priority`` (the field the scheduler orders by)."""
        ...


@runtime_checkable
class GoalStore(Protocol):
    """Read/write the OKR tree — the ``goal`` rows chorus reads for alignment, authored by horizon."""

    def upsert(self, node: GoalNode) -> str:
        """Create or update a goal node; return its id."""
        ...

    def get(self, goal_id: str) -> GoalNode | None:
        """Fetch one goal node, or ``None``."""
        ...

    def children(self, parent_id: str | None) -> list[GoalNode]:
        """The direct children of ``parent_id`` (or the roots when ``parent_id`` is ``None``)."""
        ...


@runtime_checkable
class OutcomeFeed(Protocol):
    """Subscribe to / replay *landed* outcomes — the push-driven back-pressure horizon reacts to."""

    def subscribe(self, callback: Callable[[OutcomeEvent], None]) -> Callable[[], None]:
        """Register a subscriber; return an unsubscribe handle."""
        ...

    def replay(self, *, after: str | None = None) -> Iterator[OutcomeEvent]:
        """Re-read landed outcomes from the durable log (for catch-up / cold start)."""
        ...


__all__ = [
    "GoalNode",
    "GoalStore",
    "IntakePort",
    "OutcomeEvent",
    "OutcomeFeed",
    "Priority",
]
