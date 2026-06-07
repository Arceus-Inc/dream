"""The coordination board — a sqlite-WAL CAS store (Spec 08, decision #2).

``board.sqlite`` is the *only* sanctioned cross-worktree mutable state and the
single source of truth *of now* (git is the source of truth *of record*). It is
deliberately low-level: it knows rows and transactions, not claim policy (that is
``ClaimManager``). Every mutation runs under ``BEGIN IMMEDIATE`` so concurrent
writers across processes serialize and exactly one wins; ``busy_timeout`` makes a
contending writer wait rather than fail.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dream.coordination._types import Claim, ClaimState

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS claims (
    task_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    checkout_run_id TEXT,
    execution_run_id TEXT,
    claimed_by TEXT,
    claimed_at_ms INTEGER,
    lease_expires_at_ms INTEGER,
    last_heartbeat_at_ms INTEGER
)
"""

_UPSERT = """
INSERT INTO claims (
    task_id, state, checkout_run_id, execution_run_id,
    claimed_by, claimed_at_ms, lease_expires_at_ms, last_heartbeat_at_ms
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(task_id) DO UPDATE SET
    state = excluded.state,
    checkout_run_id = excluded.checkout_run_id,
    execution_run_id = excluded.execution_run_id,
    claimed_by = excluded.claimed_by,
    claimed_at_ms = excluded.claimed_at_ms,
    lease_expires_at_ms = excluded.lease_expires_at_ms,
    last_heartbeat_at_ms = excluded.last_heartbeat_at_ms
"""

_SELECT = """
SELECT task_id, state, checkout_run_id, execution_run_id,
       claimed_by, claimed_at_ms, lease_expires_at_ms, last_heartbeat_at_ms
FROM claims WHERE task_id = ?
"""

_COUNT_EXECUTING = """
SELECT COUNT(*) FROM claims WHERE state = 'executing' AND lease_expires_at_ms > ?
"""


def _row_to_claim(row: tuple[object, ...]) -> Claim:
    return Claim(
        task_id=str(row[0]),
        state=_as_state(row[1]),
        checkout_run_id=_as_str(row[2]),
        execution_run_id=_as_str(row[3]),
        claimed_by=_as_str(row[4]),
        claimed_at_ms=_as_int(row[5]),
        lease_expires_at_ms=_as_int(row[6]),
        last_heartbeat_at_ms=_as_int(row[7]),
    )


def _claim_params(claim: Claim) -> tuple[object, ...]:
    return (
        claim.task_id,
        claim.state,
        claim.checkout_run_id,
        claim.execution_run_id,
        claim.claimed_by,
        claim.claimed_at_ms,
        claim.lease_expires_at_ms,
        claim.last_heartbeat_at_ms,
    )


class BoardTransaction:
    """Row operations bound to one in-progress ``BEGIN IMMEDIATE`` transaction."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def read(self, task_id: str) -> Claim | None:
        cur = self._conn.execute(_SELECT, (task_id,))
        row = cur.fetchone()
        return _row_to_claim(row) if row is not None else None

    def upsert(self, claim: Claim) -> None:
        self._conn.execute(_UPSERT, _claim_params(claim))

    def count_executing(self, *, now_ms: int) -> int:
        cur = self._conn.execute(_COUNT_EXECUTING, (now_ms,))
        return int(cur.fetchone()[0])


class BoardStore:
    """A connection to ``board.sqlite`` exposing CAS transactions + reads."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → autocommit; we issue BEGIN IMMEDIATE explicitly.
        self._conn = sqlite3.connect(str(self._path), isolation_level=None)
        applied = str(self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        if applied.lower() != "wal":
            self._conn.close()
            raise RuntimeError(f"board.sqlite WAL mode unavailable; got {applied!r}")
        self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self._conn.execute(_CREATE_TABLE)

    def __enter__(self) -> BoardStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read(self, task_id: str) -> Claim | None:
        """Autocommit point read (no transaction)."""
        cur = self._conn.execute(_SELECT, (task_id,))
        row = cur.fetchone()
        return _row_to_claim(row) if row is not None else None

    @contextmanager
    def transaction(self) -> Iterator[BoardTransaction]:
        """Run a CAS transaction: ``BEGIN IMMEDIATE`` → body → COMMIT / ROLLBACK."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield BoardTransaction(self._conn)
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def journal_mode(self) -> str:
        return str(self._conn.execute("PRAGMA journal_mode").fetchone()[0])

    def has_claims_table(self) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims'"
        )
        return cur.fetchone() is not None

    def close(self) -> None:
        self._conn.close()


def _as_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"expected TEXT column, got {type(value).__name__}")


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, int):
        return value
    raise TypeError(f"expected INTEGER column, got {type(value).__name__}")


def _as_state(value: object) -> ClaimState:
    if value in ("claimed", "executing", "releasing"):
        return value
    raise ValueError(f"unknown claim state in board: {value!r}")


__all__ = ["BoardStore", "BoardTransaction"]
