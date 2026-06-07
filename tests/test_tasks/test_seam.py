"""Spec 07 slice 2 — the durable↔ephemeral seam.

A ``BackgroundTaskManager`` ``TaskRecord`` is ephemeral; an exec-plan
``Ledger`` is durable. The seam is a **completion listener** that, when
a task tagged with a ledger reference reaches a terminal state, updates
the corresponding ledger entry and commits the JSON to disk.

Spec 07 §"Completion → durable update (the seam)":

    A registered ``CompletionListener`` receives the terminal
    ``TaskRecord``. If the task was advancing a ledger entry, the
    listener updates that entry's ``status``/``passes``/``notes`` and
    commits the ledger.

The tag is carried in ``TaskRecord.metadata`` (``task_id`` + ``entry_id``
+ ledger path), which the *spawner* — not the manager — populates.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from dream.tasks._ledger import Ledger, LedgerEntry, write_ledger
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._seam import make_ledger_completion_listener
from dream.tasks._types import TaskRecord


def _seed_ledger(path: Path, *, entry_id: str = "e1") -> Ledger:
    now = datetime.now(UTC)
    ledger = Ledger(
        task_id="T1",
        state="active",
        created_at=now,
        updated_at=now,
        entries=(
            LedgerEntry(id=entry_id, description="run the thing", status="in_progress"),
        ),
    )
    write_ledger(path, ledger)
    return ledger


async def test_completion_updates_and_commits_ledger(tmp_path: Path) -> None:
    """A natural-exit success transitions the ledger entry to done."""
    ledger_path = tmp_path / "T1.json"
    _seed_ledger(ledger_path)

    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    mgr.register_completion_listener(make_ledger_completion_listener())

    record = await mgr.create_shell_task(
        description="advance e1",
        cwd=tmp_path,
        argv=[sys.executable, "-c", "import sys; sys.exit(0)"],
        metadata={
            "task_id": "T1",
            "entry_id": "e1",
            "ledger_path": str(ledger_path),
        },
    )

    # Wait for completion + listener flush
    while True:
        t = mgr.get_task(record.id)
        assert t is not None
        if t.status in {"completed", "failed", "killed"}:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)

    from dream.tasks._ledger import read_ledger
    after = read_ledger(ledger_path)
    assert after.entries[0].status == "done"
    assert after.entries[0].passes is False  # evaluator_enabled=False, no claim
    # the completion was recorded in notes (append-only)
    assert any("completed" in n for n in after.entries[0].notes)


async def test_completion_failure_marks_blocked(tmp_path: Path) -> None:
    """A non-zero exit blocks the entry instead of marking it done."""
    ledger_path = tmp_path / "T1.json"
    _seed_ledger(ledger_path)

    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    mgr.register_completion_listener(make_ledger_completion_listener())

    record = await mgr.create_shell_task(
        description="fail e1",
        cwd=tmp_path,
        argv=[sys.executable, "-c", "import sys; sys.exit(3)"],
        metadata={
            "task_id": "T1",
            "entry_id": "e1",
            "ledger_path": str(ledger_path),
        },
    )
    while True:
        t = mgr.get_task(record.id)
        assert t is not None
        if t.status in {"completed", "failed", "killed"}:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)

    from dream.tasks._ledger import read_ledger
    after = read_ledger(ledger_path)
    assert after.entries[0].status == "blocked"
    assert any("return_code=3" in n or "failed" in n for n in after.entries[0].notes)


async def test_seam_ignores_untagged_tasks(tmp_path: Path) -> None:
    """A task without ``task_id``/``entry_id``/``ledger_path`` metadata is a
    no-op for the listener — it must not raise."""
    ledger_path = tmp_path / "T1.json"
    _seed_ledger(ledger_path)
    snapshot_before = ledger_path.read_text(encoding="utf-8")

    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    mgr.register_completion_listener(make_ledger_completion_listener())

    record = await mgr.create_shell_task(
        description="untagged",
        cwd=tmp_path,
        argv=[sys.executable, "-c", "pass"],
    )
    while True:
        t = mgr.get_task(record.id)
        assert t is not None
        if t.status in {"completed", "failed", "killed"}:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)

    assert ledger_path.read_text(encoding="utf-8") == snapshot_before


def test_seam_concurrent_completions_do_not_lose_updates(tmp_path: Path) -> None:
    """Two completions on the *same* ledger file must serialise via the
    per-ledger lock — without it the unlocked read-modify-write loses one
    entry's transition (#58)."""
    import threading

    ledger_path = tmp_path / "T1.json"
    now = datetime.now(UTC)
    ledger = Ledger(
        task_id="T1",
        state="active",
        created_at=now,
        updated_at=now,
        entries=(
            LedgerEntry(id="e1", description="first", status="in_progress"),
            LedgerEntry(id="e2", description="second", status="pending"),
        ),
    )
    write_ledger(ledger_path, ledger)

    listener = make_ledger_completion_listener()

    def _terminal_record(entry_id: str) -> TaskRecord:
        return TaskRecord(
            id=f"task-{entry_id}",
            type="local_bash",
            status="completed",
            description="done",
            cwd=str(tmp_path),
            output_file=tmp_path / f"{entry_id}.log",
            return_code=0,
            metadata={
                "task_id": "T1",
                "entry_id": entry_id,
                "ledger_path": str(ledger_path),
            },
        )

    barrier = threading.Barrier(2)

    def fire(entry_id: str) -> None:
        barrier.wait()  # maximise overlap of the read-modify-write windows
        listener(_terminal_record(entry_id))

    threads = [
        threading.Thread(target=fire, args=("e1",)),
        threading.Thread(target=fire, args=("e2",)),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    from dream.tasks._ledger import read_ledger

    after = read_ledger(ledger_path)
    by_id = {e.id: e for e in after.entries}
    # Both completions must be reflected; neither overwrites the other.
    assert by_id["e1"].status == "done"
    assert by_id["e2"].status == "done"
    assert by_id["e1"].notes  # the completion note landed
    assert by_id["e2"].notes


async def test_seam_killed_task_marks_blocked(tmp_path: Path) -> None:
    """Stopping a tagged task leaves its entry blocked rather than done."""
    ledger_path = tmp_path / "T1.json"
    _seed_ledger(ledger_path)

    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    mgr.register_completion_listener(make_ledger_completion_listener())

    record = await mgr.create_shell_task(
        description="long-running tagged",
        cwd=tmp_path,
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        metadata={
            "task_id": "T1",
            "entry_id": "e1",
            "ledger_path": str(ledger_path),
        },
    )
    await asyncio.sleep(0.15)
    await mgr.stop_task(record.id)
    await asyncio.sleep(0.1)

    from dream.tasks._ledger import read_ledger
    after = read_ledger(ledger_path)
    assert after.entries[0].status == "blocked"
