"""Wake notes — the durable queue between cron firings and the next wake.

The timed-note pattern (OpenClaw ``wakeMode: next-heartbeat``): a cron
job that targets the wake doesn't spawn anything — it drops a note the
next heartbeat reads. Same atomic drop-dir discipline as the command
inbox; corrupt files are removed, never wedge the queue.
"""

from __future__ import annotations

from pathlib import Path

from dream.runtime._wake_notes import WakeNoteStore


def test_add_then_drain_round_trips_in_order(tmp_path: Path) -> None:
    store = WakeNoteStore(tmp_path / "notes")
    store.add("check the digest backlog", source="rolling-digest")
    store.add("weekly report due", source="weekly-report")

    drained = store.drain()
    assert [n.text for n in drained] == [
        "check the digest backlog",
        "weekly report due",
    ]
    assert drained[0].source == "rolling-digest"
    # Drained means consumed.
    assert store.drain() == []


def test_drain_missing_dir_is_empty(tmp_path: Path) -> None:
    assert WakeNoteStore(tmp_path / "nowhere").drain() == []


def test_corrupt_note_removed_not_fatal(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    store = WakeNoteStore(notes_dir)
    store.add("good note", source="cron-a")
    (notes_dir / "0000000000.000000_bad.json").write_text("{torn", encoding="utf-8")

    drained = store.drain()
    assert [n.text for n in drained] == ["good note"]
    assert list(notes_dir.glob("*.json")) == []


def test_pending_count_without_consuming(tmp_path: Path) -> None:
    store = WakeNoteStore(tmp_path / "notes")
    assert store.pending() == 0
    store.add("note", source="cron-a")
    assert store.pending() == 1
    assert store.pending() == 1  # peeking does not consume
