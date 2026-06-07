"""The durable-ownership mirror seam (Spec 08, decision #3 / AC #3).

``ClaimManager`` calls a ``ClaimMirror`` *after* a claim/release CAS commits, so
ownership lands in the of-record `#07` ledger (the source the board is rebuilt
from). The seam keeps the coordination core free of git/ledger coupling: the
default is a no-op, and ``LedgerClaimMirror`` is the reference adapter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dream.tasks._ledger import ClaimRecord


@runtime_checkable
class ClaimMirror(Protocol):
    """Persist ownership facts to the of-record store at claim boundaries."""

    def on_grant(self, task_id: str, record: ClaimRecord) -> None:
        """Record that ``task_id`` was granted/reclaimed under ``record``."""
        ...

    def on_release(self, task_id: str) -> None:
        """Record that ``task_id`` was cleanly released."""
        ...


class NoopClaimMirror:
    """Default mirror that persists nothing (board-only operation)."""

    def on_grant(self, task_id: str, record: ClaimRecord) -> None:
        del task_id, record

    def on_release(self, task_id: str) -> None:
        del task_id


__all__ = ["ClaimMirror", "NoopClaimMirror"]
