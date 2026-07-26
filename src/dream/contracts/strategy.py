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

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

# chorus's coarse scheduler priority (``TaskPriority`` values). The strategy layer keeps a rich numeric
# score internally and maps to one of these at the intake boundary.
Priority = str  # "high" | "medium" | "low"


class LandedPhase(StrEnum):
    """The scheduler's single authoritative landing phase (Phase 0 seam).

    Emitted once at the chorus scheduler choke point; bridge and horizon project it mechanically.
    """

    TERMINAL_PASS = "terminal_pass"
    TERMINAL_FAIL = "terminal_fail"
    NEEDS_REWORK = "needs_rework"
    DELEGATED = "delegated"
    STRANDED = "stranded"
    CANCELLED = "cancelled"


class RecoveryHint(StrEnum):
    """What horizon should infer for recovery — derived from :class:`LandedPhase`, not prose."""

    NONE = "none"
    REWORK = "rework"
    WAIT_FOR_CHILDREN = "wait_for_children"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class LandedOutcome:
    """The typed outcome chorus derives once when a beat lands (Phase 0).

    ``strategy_passed()`` is the horizon down-rank signal: ``False`` for ``TERMINAL_FAIL`` and
    ``NEEDS_REWORK`` (locked decision — rework is not a pass). ``None`` when the phase is not a
    terminal verdict (delegation, strand, cancel).
    """

    phase: LandedPhase
    summary: str
    dod_status: str | None = None
    disposition: str | None = None
    diagnostic: str = ""
    execution_mode: str | None = None

    def strategy_passed(self) -> bool | None:
        if self.phase is LandedPhase.TERMINAL_PASS:
            return True
        if self.phase in (LandedPhase.TERMINAL_FAIL, LandedPhase.NEEDS_REWORK):
            return False
        return None

    def recovery_hint(self) -> RecoveryHint:
        return _RECOVERY_HINT_BY_PHASE[self.phase]

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {
            "phase": self.phase.value,
            "summary": self.summary,
        }
        if self.dod_status is not None:
            payload["dod_status"] = self.dod_status
        if self.disposition is not None:
            payload["disposition"] = self.disposition
        if self.diagnostic:
            payload["diagnostic"] = self.diagnostic
        if self.execution_mode is not None:
            payload["execution_mode"] = self.execution_mode
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> LandedOutcome:
        raw_phase = data.get("phase")
        if not isinstance(raw_phase, str):
            raise ValueError("phase is required")
        try:
            phase = LandedPhase(raw_phase)
        except ValueError as exc:
            raise ValueError(f"unknown phase: {raw_phase!r}") from exc
        summary = data.get("summary")
        if not isinstance(summary, str):
            raise ValueError("summary is required")
        dod_status = data.get("dod_status")
        disposition = data.get("disposition")
        diagnostic = data.get("diagnostic")
        execution_mode = data.get("execution_mode")
        return cls(
            phase=phase,
            summary=summary,
            dod_status=dod_status if isinstance(dod_status, str) else None,
            disposition=disposition if isinstance(disposition, str) else None,
            diagnostic=diagnostic if isinstance(diagnostic, str) else "",
            execution_mode=execution_mode if isinstance(execution_mode, str) else None,
        )


_RECOVERY_HINT_BY_PHASE: dict[LandedPhase, RecoveryHint] = {
    LandedPhase.TERMINAL_PASS: RecoveryHint.NONE,
    LandedPhase.TERMINAL_FAIL: RecoveryHint.NONE,
    LandedPhase.NEEDS_REWORK: RecoveryHint.REWORK,
    LandedPhase.DELEGATED: RecoveryHint.WAIT_FOR_CHILDREN,
    LandedPhase.STRANDED: RecoveryHint.ESCALATE,
    LandedPhase.CANCELLED: RecoveryHint.NONE,
}


@dataclass(frozen=True)
class GoalNode:
    """A node in the OKR/alignment tree, as the strategy layer sees it.

    Mirrors the fields chorus reads from the ``goal`` row (identity/level/status/parent) plus the small
    strategy fields horizon steers with (score/health/metric/target). Strategy-only depth (evidence,
    rationale, decision log) lives in horizon's own store, keyed by ``id`` — not here.
    """

    id: str
    title: str
    level: str = "goal"  # goal|task_intent — Decisions are horizon-only and never reach this seam
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
    ``outcome.landed`` / ``task.status`` / ``recovery.escalated`` without importing chorus.
    """

    kind: str  # e.g. "outcome.landed" | "task.status" | "recovery.escalated"
    task_id: str | None = None
    goal_id: str | None = None
    status: str | None = None  # "done" | "failed" | "blocked" | ...
    passed: bool | None = None  # the DoD verdict, when the event carries one
    phase: str | None = None  # :class:`LandedPhase` value when ``kind`` is ``outcome.landed``
    recovery_hint: str | None = None  # :class:`RecoveryHint` value — mechanical, not parsed from detail
    summary: str | None = None  # one-line landed summary (distinct from ``detail`` diagnostic text)
    detail: str = ""
    parent_task_id: str | None = None
    root_task_id: str | None = None
    team_id: str | None = None
    execution_mode: str | None = None
    is_root_outcome: bool = False
    event_id: str | None = None
    task_revision: int | None = None


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
    "LandedOutcome",
    "LandedPhase",
    "OutcomeEvent",
    "OutcomeFeed",
    "Priority",
    "RecoveryHint",
]
