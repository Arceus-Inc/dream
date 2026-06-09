"""Spec 08 — ClaimManager: two-lock claim, lease, cap, reclaim.

Stateless API: ``claim`` returns the minted ownership token; the caller threads
it back to ``begin_execution``/``release``, and the liveness token to
``heartbeat``. Time is driven by an injected ``FakeClock``.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from dream.coordination._board import BoardStore
from dream.coordination._claim import ClaimManager, ClaimResult, Denied, Granted
from dream.utils.clock import FakeClock

LEASE_S = 900  # 15 min
LEASE_MS = LEASE_S * 1000


def _mgr(
    path: Path, clock: FakeClock, *, runner: str = "runner-a", cap: int = 4
) -> ClaimManager:
    return ClaimManager(
        BoardStore(path),
        clock=clock,
        runner_id=runner,
        lease_seconds=LEASE_S,
        max_concurrent_runs=cap,
    )


def _granted(result: object) -> Granted:
    assert isinstance(result, Granted), f"expected Granted, got {result!r}"
    return result


# --- claim ------------------------------------------------------------------


def test_claim_grants_and_mints_checkout(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    claim = _granted(mgr.claim("T1")).claim
    assert claim.checkout_run_id is not None and claim.checkout_run_id.startswith("co_")
    assert claim.state == "claimed"
    assert claim.claimed_by == "runner-a"
    assert claim.lease_expires_at_ms == 1_000 + LEASE_MS


def test_claim_denied_when_live_owner(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    _granted(_mgr(path, clock, runner="a").claim("T1"))
    result = _mgr(path, clock, runner="b").claim("T1")
    assert isinstance(result, Denied)


def test_claim_reclaimable_after_lease_expiry(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    _granted(_mgr(path, clock, runner="a").claim("T1"))
    clock.advance(LEASE_MS + 1)
    assert isinstance(_mgr(path, clock, runner="b").claim("T1"), Granted)


# --- begin_execution + capacity --------------------------------------------


def test_begin_execution_mints_execution_token(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    claim = _granted(mgr.begin_execution("T1", checkout_run_id=co)).claim
    assert claim.state == "executing"
    assert claim.execution_run_id is not None and claim.execution_run_id.startswith("ex_")


def test_begin_execution_requires_owner_token(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    mgr.claim("T1")
    assert isinstance(mgr.begin_execution("T1", checkout_run_id="co_wrong"), Denied)


def test_begin_execution_denied_at_capacity(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock, cap=1)
    co1 = _granted(mgr.claim("T1")).claim.checkout_run_id
    _granted(mgr.begin_execution("T1", checkout_run_id=co1))  # fills the one slot
    co2 = _granted(mgr.claim("T2")).claim.checkout_run_id
    assert isinstance(mgr.begin_execution("T2", checkout_run_id=co2), Denied)


def test_capacity_frees_when_lease_expires(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock, cap=1)
    co1 = _granted(mgr.claim("T1")).claim.checkout_run_id
    _granted(mgr.begin_execution("T1", checkout_run_id=co1))
    clock.advance(LEASE_MS + 1)  # T1's executing slot lapses
    co2 = _granted(mgr.claim("T2")).claim.checkout_run_id
    assert isinstance(mgr.begin_execution("T2", checkout_run_id=co2), Granted)


# --- heartbeat --------------------------------------------------------------


def test_heartbeat_renews_lease(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    ex = _granted(mgr.begin_execution("T1", checkout_run_id=co)).claim.execution_run_id
    clock.advance(60_000)
    assert mgr.heartbeat("T1", execution_run_id=ex) is True
    assert mgr.get("T1").lease_expires_at_ms == clock.now_ms() + LEASE_MS  # type: ignore[union-attr]


def test_heartbeat_false_when_not_executor(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    mgr.begin_execution("T1", checkout_run_id=co)
    assert mgr.heartbeat("T1", execution_run_id="ex_wrong") is False


def test_heartbeat_false_after_reclaim(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    a = _mgr(path, clock, runner="a")
    co = _granted(a.claim("T1")).claim.checkout_run_id
    ex = _granted(a.begin_execution("T1", checkout_run_id=co)).claim.execution_run_id
    clock.advance(LEASE_MS + 1)
    _granted(_mgr(path, clock, runner="b").reclaim("T1"))  # b takes over
    assert a.heartbeat("T1", execution_run_id=ex) is False


# --- release + reclaim ------------------------------------------------------


def test_release_clears_tokens(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    assert mgr.release("T1", checkout_run_id=co) is True
    row = mgr.get("T1")
    assert row is not None and row.checkout_run_id is None and row.state == "releasing"


def test_release_false_when_not_owner(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    mgr.claim("T1")
    assert mgr.release("T1", checkout_run_id="co_wrong") is False


def test_reclaim_denied_when_live(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    _granted(_mgr(path, clock, runner="a").claim("T1"))
    assert isinstance(_mgr(path, clock, runner="b").reclaim("T1"), Denied)


def test_reclaim_grants_fresh_checkout_after_expiry(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    old = _granted(_mgr(path, clock, runner="a").claim("T1")).claim.checkout_run_id
    clock.advance(LEASE_MS + 1)
    new = _granted(_mgr(path, clock, runner="b").reclaim("T1")).claim.checkout_run_id
    assert new is not None and new != old


def test_get_returns_none_for_unknown(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path / "b.sqlite", FakeClock())
    assert mgr.get("nope") is None


# --- resilience + invariants (review fixes) ---------------------------------


def test_begin_execution_is_not_re_runnable(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    _granted(mgr.begin_execution("T1", checkout_run_id=co))
    # A second begin would sever the live execution token — refuse it.
    assert isinstance(mgr.begin_execution("T1", checkout_run_id=co), Denied)


def test_lease_expiry_is_wallclock_at_boundary(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    _granted(_mgr(path, clock, runner="a").claim("T1"))
    clock.advance(LEASE_MS)  # now == lease_expires_at_ms → expired (strict >)
    assert isinstance(_mgr(path, clock, runner="b").claim("T1"), Granted)


def test_heartbeat_tolerates_transient_lock_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    ex = _granted(mgr.begin_execution("T1", checkout_run_id=co)).claim.execution_run_id

    def _locked() -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(mgr._board, "transaction", _locked)
    assert mgr.heartbeat("T1", execution_run_id=ex) is False  # no raise


def test_heartbeat_propagates_non_transient_db_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corruption / schema / I/O failure must not be masked as a missed
    heartbeat (``False``) — it has to surface so it is observable."""
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id
    ex = _granted(mgr.begin_execution("T1", checkout_run_id=co)).claim.execution_run_id

    def _corrupt() -> object:
        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(mgr._board, "transaction", _corrupt)
    with pytest.raises(sqlite3.OperationalError, match="malformed"):
        mgr.heartbeat("T1", execution_run_id=ex)


def test_release_returns_true_when_mirror_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership tokens are cleared in the board before the mirror write; a
    mirror failure must not flip a successful release into a hard error."""
    clock = FakeClock(start_ms=1_000)
    mgr = _mgr(tmp_path / "b.sqlite", clock)
    co = _granted(mgr.claim("T1")).claim.checkout_run_id

    def _boom(task_id: str) -> None:
        raise RuntimeError("mirror down")

    monkeypatch.setattr(mgr._mirror, "on_release", _boom)
    assert mgr.release("T1", checkout_run_id=co) is True
    row = mgr.get("T1")
    assert row is not None and row.checkout_run_id is None


def test_concurrent_reclaim_exactly_one_winner(tmp_path: Path) -> None:
    clock = FakeClock(start_ms=1_000)
    path = tmp_path / "b.sqlite"
    _granted(_mgr(path, clock, runner="a").claim("T1"))
    clock.advance(LEASE_MS + 1)  # lease expired → reclaimable

    results: list[ClaimResult] = []

    def worker(runner: str) -> None:
        results.append(_mgr(path, clock, runner=runner).reclaim("T1"))

    threads = [threading.Thread(target=worker, args=(f"r{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    granted = [r for r in results if isinstance(r, Granted)]
    assert len(granted) == 1
    assert len(results) == 4
