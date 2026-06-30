"""ClaimManager — the two-lock claim policy over the board (Spec 08).

Stateless: every method takes the ``task_id`` plus the token that proves the
caller's right to act (``checkout_run_id`` for ownership, ``execution_run_id`` for
liveness). The board (`#08` decision #2) provides the CAS; this layer adds token
minting, lease math (via the injected ``Clock``), the 409-never-retry denial, and
the board-backed concurrency cap. Graceful recovery of a dead owner — the typed
resume/restart/abandon decision and the liveness tri-state — is ``#10p5``; here a
reclaim is a blunt fresh claim of an expired lease.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from dream.coordination._board import BoardStore
from dream.coordination._mirror import ClaimMirror, NoopClaimMirror
from dream.coordination._types import Claim
from dream.tasks._ledger import ClaimRecord
from dream.utils.clock import Clock

_CHECKOUT_PREFIX = "co"
_EXECUTION_PREFIX = "ex"


@dataclass(frozen=True)
class Granted:
    """A claim/begin/reclaim succeeded; ``claim`` carries the minted token(s).

    ``mirror_error`` is non-None when the CAS committed but the durable mirror
    write failed afterwards — the claim is valid (board is source of *now*), but
    the of-record ledger is stale; surfaced as data, never raised.
    """

    claim: Claim
    mirror_error: str | None = None


@dataclass(frozen=True)
class Denied:
    """A claim/begin/reclaim was refused (the 409). Do not retry the same task."""

    reason: str


ClaimResult = Granted | Denied


class ClaimManager:
    """Manage two-lock claims for one runner against a coordination board."""

    def __init__(
        self,
        board: BoardStore,
        *,
        clock: Clock,
        runner_id: str,
        max_concurrent_runs: int,
        lease_seconds: float = 900.0,
        mirror: ClaimMirror | None = None,
    ) -> None:
        self._board = board
        self._clock = clock
        self._runner_id = runner_id
        self._max_concurrent_runs = max_concurrent_runs
        self._lease_ms = int(lease_seconds * 1000)
        self._mirror: ClaimMirror = mirror or NoopClaimMirror()
        self._last_mirror_error: str | None = None

    @property
    def last_mirror_error(self) -> str | None:
        """Most recent mirror failure (best-effort ops), or None if clean."""
        return self._last_mirror_error

    def claim(self, task_id: str) -> ClaimResult:
        """Win ownership of ``task_id``; deny if a live owner already holds it."""
        return self._acquire(task_id, deny="is held by another runner")

    def reclaim(self, task_id: str) -> ClaimResult:
        """Reclaim an expired lease as a fresh claim (10p5 adds the recovery decision)."""
        return self._acquire(task_id, deny="lease is still live")

    def _acquire(self, task_id: str, *, deny: str) -> ClaimResult:
        now = self._clock.now_ms()
        checkout = _mint(_CHECKOUT_PREFIX)
        new_claim = Claim(
            task_id=task_id,
            state="claimed",
            checkout_run_id=checkout,
            claimed_by=self._runner_id,
            claimed_at_ms=now,
            lease_expires_at_ms=now + self._lease_ms,
            last_heartbeat_at_ms=now,
        )
        with self._board.transaction() as tx:
            row = tx.read(task_id)
            if row is not None and _is_live_owner(row, now):
                return Denied(f"task {task_id!r} {deny}")
            tx.upsert(new_claim)
        # Mirror ownership AFTER the CAS commits (board is source of *now*). A
        # mirror failure must not invalidate a committed claim, so it is caught
        # and surfaced as data rather than left to block the task for a lease.
        record = ClaimRecord(
            checkout_run_id=checkout, claimed_by=self._runner_id, claimed_at=_ms_to_dt(now)
        )
        return Granted(new_claim, mirror_error=self._safe_grant_mirror(task_id, record))

    def _safe_grant_mirror(self, task_id: str, record: ClaimRecord) -> str | None:
        try:
            self._mirror.on_grant(task_id, record)
        except Exception as exc:  # best-effort: surface, don't raise (board is committed)
            return f"{type(exc).__name__}: {exc}"
        return None

    def begin_execution(self, task_id: str, *, checkout_run_id: str) -> ClaimResult:
        """Transition an owned claim to ``executing``, gated by the concurrency cap."""
        now = self._clock.now_ms()
        with self._board.transaction() as tx:
            row = tx.read(task_id)
            if row is None or row.checkout_run_id != checkout_run_id:
                return Denied(f"task {task_id!r} is not owned by this checkout")
            if row.state == "executing":
                # Idempotency guard: a second begin would mint a new execution
                # token and silently sever the existing heartbeat (#7 invariant:
                # one executing run per task).
                return Denied(f"task {task_id!r} is already executing")
            if not _lease_live(row, now):
                return Denied(f"task {task_id!r} lease has expired; reclaim first")
            if tx.count_executing(now_ms=now) >= self._max_concurrent_runs:
                return Denied("at capacity: max_concurrent_runs reached")
            claim = replace(
                row,
                state="executing",
                execution_run_id=_mint(_EXECUTION_PREFIX),
                lease_expires_at_ms=now + self._lease_ms,
                last_heartbeat_at_ms=now,
            )
            tx.upsert(claim)
        return Granted(claim)

    def heartbeat(self, task_id: str, *, execution_run_id: str) -> bool:
        """Renew the lease; ``False`` if not the live executor or the beat missed.

        A transient board-lock failure returns ``False`` rather than raising, so
        a missed beat never aborts the turn (AC #11) — the lease simply ages
        until the next successful beat.
        """
        now = self._clock.now_ms()
        try:
            with self._board.transaction() as tx:
                row = tx.read(task_id)
                if row is None or row.execution_run_id != execution_run_id:
                    return False
                tx.upsert(
                    replace(
                        row, lease_expires_at_ms=now + self._lease_ms, last_heartbeat_at_ms=now
                    )
                )
        except sqlite3.OperationalError as exc:
            # Only a transient lock/busy contention is a "missed beat" we
            # absorb (AC #11). Corruption, schema, or I/O errors must surface
            # so they are observable and recoverable, not masked as False.
            if _is_lock_contention(exc):
                return False
            raise
        return True

    def release(self, task_id: str, *, checkout_run_id: str) -> bool:
        """Clear both tokens on clean release; ``False`` if not the owner."""
        with self._board.transaction() as tx:
            row = tx.read(task_id)
            if row is None or row.checkout_run_id != checkout_run_id:
                return False
            tx.upsert(
                replace(row, state="releasing", checkout_run_id=None, execution_run_id=None)
            )
        # The board CAS already cleared ownership, so the release succeeded.
        # The durable mirror is best-effort (board is source of *now*): a
        # mirror failure must not flip a successful release into a hard error.
        self._safe_release_mirror(task_id)
        return True

    def _safe_release_mirror(self, task_id: str) -> None:
        # Best-effort: the board CAS already committed the release, so a mirror
        # failure must not raise. Mirrors the catch in ``_safe_grant_mirror``.
        try:
            self._mirror.on_release(task_id)
        except Exception as exc:
            self._last_mirror_error = f"release({task_id}): {type(exc).__name__}: {exc}"

    def get(self, task_id: str) -> Claim | None:
        return self._board.read(task_id)


def _is_lock_contention(exc: sqlite3.OperationalError) -> bool:
    """True only for the transient lock/busy contention SQLite signals when a
    ``BEGIN IMMEDIATE`` cannot acquire the write lock within ``busy_timeout``.

    Everything else (``disk image is malformed``, ``no such table``, disk I/O)
    is a real fault that must propagate rather than be swallowed as a missed
    heartbeat.
    """
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _mint(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _lease_live(row: Claim, now_ms: int) -> bool:
    return row.lease_expires_at_ms is not None and row.lease_expires_at_ms > now_ms


def _is_live_owner(row: Claim, now_ms: int) -> bool:
    """A row with an owner, an unexpired lease, and a non-terminal state."""
    return (
        row.checkout_run_id is not None
        and row.state != "releasing"
        and _lease_live(row, now_ms)
    )


__all__ = ["ClaimManager", "ClaimResult", "Denied", "Granted"]
