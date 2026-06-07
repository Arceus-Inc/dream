"""Spec 08 — BoardStore: sqlite-WAL CAS primitives for the coordination board.

The concurrency test uses one BoardStore (= one connection) per thread against a
shared file — the in-process analogue of multiple runner *processes* contending
on the same board. ``BEGIN IMMEDIATE`` + ``busy_timeout`` must serialize writers
with no lost update.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import pytest

from dream.coordination._board import BoardStore
from dream.coordination._types import Claim


def _store(tmp_path: Path) -> BoardStore:
    return BoardStore(tmp_path / "coordination" / "board.sqlite")


def _claim(task_id: str, **kw: object) -> Claim:
    return Claim(task_id=task_id, state=kw.pop("state", "claimed"), **kw)  # type: ignore[arg-type]


def test_open_creates_table_in_wal_mode(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.journal_mode().lower() == "wal"
    assert store.has_claims_table() is True
    store.close()


def test_read_missing_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.read("nope") is None
    store.close()


def test_upsert_then_read_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claim = Claim(
        task_id="T1",
        state="executing",
        checkout_run_id="co_1",
        execution_run_id="ex_1",
        claimed_by="runner-a",
        claimed_at_ms=1000,
        lease_expires_at_ms=2000,
        last_heartbeat_at_ms=1500,
    )
    with store.transaction() as tx:
        tx.upsert(claim)
    assert store.read("T1") == claim
    store.close()


def test_upsert_updates_existing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.transaction() as tx:
        tx.upsert(_claim("T1", claimed_by="a"))
    with store.transaction() as tx:
        tx.upsert(_claim("T1", claimed_by="b"))
    row = store.read("T1")
    assert row is not None and row.claimed_by == "b"
    store.close()


def test_count_executing_respects_state_and_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.transaction() as tx:
        tx.upsert(_claim("live", state="executing", lease_expires_at_ms=5000))
        tx.upsert(_claim("expired", state="executing", lease_expires_at_ms=100))
        tx.upsert(_claim("merely_claimed", state="claimed", lease_expires_at_ms=5000))
        assert tx.count_executing(now_ms=1000) == 1  # only "live"
    store.close()


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.transaction() as tx:
        tx.upsert(_claim("T1"))
    assert store.read("T1") is not None
    store.close()


def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            tx.upsert(_claim("T1"))
            raise RuntimeError("boom")
    assert store.read("T1") is None  # rolled back
    store.close()


def test_concurrent_transactions_serialize_no_lost_update(tmp_path: Path) -> None:
    path = tmp_path / "coordination" / "board.sqlite"
    seed = BoardStore(path)
    with seed.transaction() as tx:
        tx.upsert(_claim("counter", last_heartbeat_at_ms=0))
    seed.close()

    threads_n, per_thread = 4, 25

    def worker() -> None:
        store = BoardStore(path)
        try:
            for _ in range(per_thread):
                with store.transaction() as tx:
                    row = tx.read("counter")
                    assert row is not None
                    tx.upsert(
                        dataclasses.replace(
                            row, last_heartbeat_at_ms=(row.last_heartbeat_at_ms or 0) + 1
                        )
                    )
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = BoardStore(path)
    row = final.read("counter")
    assert row is not None and row.last_heartbeat_at_ms == threads_n * per_thread
    final.close()
