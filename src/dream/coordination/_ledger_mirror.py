"""LedgerClaimMirror — write the durable ownership mirror into the #07 ledger.

The reference ``ClaimMirror``: it sets the ledger's ``claim`` field under an
``exclusive_file_lock`` (the same read-modify-write discipline #07's completion
seam uses). It never shells out to git — the commit rides the existing
task-boundary commit, keeping per-claim writes to a single file mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from dream.tasks._ledger import ClaimRecord, read_ledger, write_ledger
from dream.utils.clock import Clock
from dream.utils.file_lock import exclusive_file_lock

LedgerPathFor = Callable[[str], Path]


class LedgerClaimMirror:
    """Mirror ownership into ``{task-id}.json``'s ``claim`` field."""

    def __init__(self, ledger_path_for: LedgerPathFor, *, clock: Clock) -> None:
        self._ledger_path_for = ledger_path_for
        self._clock = clock

    def on_grant(self, task_id: str, record: ClaimRecord) -> None:
        path = self._ledger_path_for(task_id)
        with exclusive_file_lock(Path(f"{path}.lock")):
            ledger = read_ledger(path)
            write_ledger(path, ledger.model_copy(update={"claim": record}))

    def on_release(self, task_id: str) -> None:
        path = self._ledger_path_for(task_id)
        with exclusive_file_lock(Path(f"{path}.lock")):
            ledger = read_ledger(path)
            if ledger.claim is None:
                return
            released = ledger.claim.model_copy(
                update={"released_at": _now_dt(self._clock)}
            )
            write_ledger(path, ledger.model_copy(update={"claim": released}))


def _now_dt(clock: Clock) -> datetime:
    return datetime.fromtimestamp(clock.now_ms() / 1000, tz=UTC)


__all__ = ["LedgerClaimMirror", "LedgerPathFor"]
