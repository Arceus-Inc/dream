"""Spec 08 — durable ownership mirror: ClaimMirror seam + LedgerClaimMirror.

The board is the source of truth *of now*; the #07 ledger `claim` field is the
of-record audit + board-rebuild source. ClaimManager calls the mirror *after* the
CAS commits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dream.coordination._board import BoardStore
from dream.coordination._claim import ClaimManager, Granted
from dream.coordination._ledger_mirror import LedgerClaimMirror
from dream.coordination._mirror import ClaimMirror, NoopClaimMirror
from dream.tasks._ledger import (
    ClaimRecord,
    Ledger,
    LedgerEntry,
    read_ledger,
    write_ledger,
)
from dream.utils.clock import FakeClock

_DT = datetime(2026, 6, 7, tzinfo=UTC)


def _seed_ledger(path: Path) -> None:
    ledger = Ledger(
        task_id="T1",
        state="active",
        created_at=_DT,
        updated_at=_DT,
        entries=(LedgerEntry(id="e1", description="do the thing"),),
    )
    write_ledger(path, ledger)


class _SpyMirror:
    """Records mirror calls (structurally a ClaimMirror)."""

    def __init__(self) -> None:
        self.grants: list[tuple[str, ClaimRecord]] = []
        self.releases: list[str] = []

    def on_grant(self, task_id: str, record: ClaimRecord) -> None:
        self.grants.append((task_id, record))

    def on_release(self, task_id: str) -> None:
        self.releases.append(task_id)


# --- Ledger.claim field -----------------------------------------------------


def test_ledger_claim_defaults_none_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "T1.json"
    _seed_ledger(path)
    assert read_ledger(path).claim is None  # backward-compatible default

    record = ClaimRecord(checkout_run_id="co_1", claimed_by="runner-a", claimed_at=_DT)
    ledger = read_ledger(path).model_copy(update={"claim": record})
    write_ledger(path, ledger)
    assert read_ledger(path).claim == record


# --- NoopClaimMirror --------------------------------------------------------


def test_noop_mirror_is_a_claim_mirror() -> None:
    mirror: ClaimMirror = NoopClaimMirror()
    mirror.on_grant("T1", ClaimRecord(checkout_run_id="co", claimed_by="a", claimed_at=_DT))
    mirror.on_release("T1")  # no error, no state


# --- LedgerClaimMirror ------------------------------------------------------


def test_ledger_mirror_on_grant_writes_claim(tmp_path: Path) -> None:
    path = tmp_path / "T1.json"
    _seed_ledger(path)
    mirror = LedgerClaimMirror(lambda _tid: path, clock=FakeClock(start_ms=1000))
    record = ClaimRecord(checkout_run_id="co_9", claimed_by="runner-a", claimed_at=_DT)
    mirror.on_grant("T1", record)
    assert read_ledger(path).claim == record


def test_ledger_mirror_on_release_sets_released_at(tmp_path: Path) -> None:
    path = tmp_path / "T1.json"
    _seed_ledger(path)
    mirror = LedgerClaimMirror(lambda _tid: path, clock=FakeClock(start_ms=2000))
    mirror.on_grant(
        "T1", ClaimRecord(checkout_run_id="co_9", claimed_by="runner-a", claimed_at=_DT)
    )
    mirror.on_release("T1")
    claim = read_ledger(path).claim
    assert claim is not None and claim.released_at is not None


def test_ledger_mirror_on_release_noop_without_claim(tmp_path: Path) -> None:
    path = tmp_path / "T1.json"
    _seed_ledger(path)
    LedgerClaimMirror(lambda _tid: path, clock=FakeClock()).on_release("T1")
    assert read_ledger(path).claim is None


# --- ClaimManager wiring ----------------------------------------------------


def test_claim_manager_calls_mirror_on_grant_and_release(tmp_path: Path) -> None:
    spy = _SpyMirror()
    mgr = ClaimManager(
        BoardStore(tmp_path / "b.sqlite"),
        clock=FakeClock(start_ms=1000),
        runner_id="runner-a",
        max_concurrent_runs=4,
        mirror=spy,
    )
    result = mgr.claim("T1")
    assert isinstance(result, Granted)
    co = result.claim.checkout_run_id
    assert spy.grants == [("T1", spy.grants[0][1])]
    assert spy.grants[0][1].checkout_run_id == co
    assert spy.grants[0][1].claimed_by == "runner-a"

    mgr.release("T1", checkout_run_id=co)  # type: ignore[arg-type]
    assert spy.releases == ["T1"]


class _ExplodingMirror:
    """A mirror whose on_grant always fails (e.g. a corrupt ledger)."""

    def on_grant(self, task_id: str, record: ClaimRecord) -> None:
        raise RuntimeError("ledger is corrupt")

    def on_release(self, task_id: str) -> None:
        pass


def test_grant_mirror_failure_is_surfaced_not_raised(tmp_path: Path) -> None:
    mgr = ClaimManager(
        BoardStore(tmp_path / "b.sqlite"),
        clock=FakeClock(start_ms=1000),
        runner_id="runner-a",
        max_concurrent_runs=4,
        mirror=_ExplodingMirror(),
    )
    result = mgr.claim("T1")
    # The CAS committed → claim is valid; the mirror error is data, not a raise.
    assert isinstance(result, Granted)
    assert result.mirror_error is not None and "RuntimeError" in result.mirror_error
    assert mgr.get("T1") is not None  # board still holds the claim


def test_claim_manager_with_ledger_mirror_end_to_end(tmp_path: Path) -> None:
    ledger_path = tmp_path / "T1.json"
    _seed_ledger(ledger_path)
    clock = FakeClock(start_ms=1000)
    mgr = ClaimManager(
        BoardStore(tmp_path / "b.sqlite"),
        clock=clock,
        runner_id="runner-a",
        max_concurrent_runs=4,
        mirror=LedgerClaimMirror(lambda _tid: ledger_path, clock=clock),
    )
    result = mgr.claim("T1")
    assert isinstance(result, Granted)
    mirrored = read_ledger(ledger_path).claim
    assert mirrored is not None and mirrored.checkout_run_id == result.claim.checkout_run_id

    mgr.release("T1", checkout_run_id=result.claim.checkout_run_id)  # type: ignore[arg-type]
    assert read_ledger(ledger_path).claim.released_at is not None  # type: ignore[union-attr]
