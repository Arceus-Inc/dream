"""Coordination — the cross-worktree claim/lease store (Spec 08).

The two-lock claim protocol over a sqlite-WAL CAS board (`board.sqlite`): who owns
a task, whether the lease is live, and who may run concurrently. Graceful recovery
from a dead runner (reconciliation, typed recovery, liveness tri-state) is the
sibling spec ``#10p5`` and is *not* part of this package.
"""

from __future__ import annotations

from dream.coordination._board import BoardStore, BoardTransaction
from dream.coordination._claim import ClaimManager, ClaimResult, Denied, Granted
from dream.coordination._ledger_mirror import LedgerClaimMirror, LedgerPathFor
from dream.coordination._mirror import ClaimMirror, NoopClaimMirror
from dream.coordination._types import Claim, ClaimState

__all__ = [
    "BoardStore",
    "BoardTransaction",
    "Claim",
    "ClaimManager",
    "ClaimMirror",
    "ClaimResult",
    "ClaimState",
    "Denied",
    "Granted",
    "LedgerClaimMirror",
    "LedgerPathFor",
    "NoopClaimMirror",
]
