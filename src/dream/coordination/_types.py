"""Coordination runtime types (Spec 08 — Task Claim & Lease).

A ``Claim`` is an immutable snapshot of one row in the coordination board. The
state machine here is the *core* set; ``#10p5`` (Runner Recovery & Liveness)
extends it with the ``lost`` / ``blocked`` states and the recovery columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ClaimState = Literal["claimed", "executing", "releasing"]
"""Core claim lifecycle. #10p5 adds ``lost`` and ``blocked``."""


@dataclass(frozen=True)
class Claim:
    """An immutable snapshot of one ``claims`` row.

    ``checkout_run_id`` is the durable ownership token; ``execution_run_id`` is
    the ephemeral liveness token minted at session start. All timestamps are
    epoch milliseconds (matching the board's integer columns and the ``Clock``
    seam).
    """

    task_id: str
    state: ClaimState
    checkout_run_id: str | None = None
    execution_run_id: str | None = None
    claimed_by: str | None = None
    claimed_at_ms: int | None = None
    lease_expires_at_ms: int | None = None
    last_heartbeat_at_ms: int | None = None


__all__ = ["Claim", "ClaimState"]
