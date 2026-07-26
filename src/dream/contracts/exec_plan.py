"""ExecPlan and the idempotency ledger.

ExecPlans are the cross-repo work unit. `horizon` submits them, `chorus`
dequeues and executes via the Harness, and the ledger guarantees that
re-submitting the same plan (same hash) does not duplicate work.

Production deployments supply their own implementation of this Protocol.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ExecPlanStatus(StrEnum):
    """Lifecycle states of an ExecPlan."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExecPlan:
    """A unit of intended work submitted to the ledger.

    `hash` is the idempotency key. Submitting the same hash twice is a
    no-op; the ledger returns the existing record.
    """

    id: str
    hash: str
    intent: str
    assigned_to: str | None = None
    expected_artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@runtime_checkable
class ExecPlanLedger(Protocol):
    """Pluggable, idempotent record of intended and executed work."""

    async def submit(self, plan: ExecPlan) -> ExecPlan:
        """Insert if new; otherwise return the existing record."""
        ...

    async def get(self, plan_id: str) -> ExecPlan | None: ...

    async def status(self, plan_id: str) -> ExecPlanStatus: ...

    async def mark_running(self, plan_id: str) -> None: ...

    async def mark_done(self, plan_id: str, artifacts: Sequence[str] = ()) -> None: ...

    async def mark_failed(self, plan_id: str, error: str) -> None: ...

    async def list_pending(self, *, limit: int = 100) -> Sequence[ExecPlan]: ...
