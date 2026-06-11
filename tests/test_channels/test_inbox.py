"""Command inbox — the runtime's inbound drop-dir (spec 15 P2 §1).

Same atomic-file pattern as ``swarm/_mailbox``: one JSON file per
command, unique names, atomic writes, oldest-first drain. Corrupt files
are removed and reported rather than wedging the channel forever.
"""

from __future__ import annotations

from pathlib import Path

from dream.channels import (
    Ack,
    CommandInbox,
    StatusCommand,
    SubmitTaskCommand,
    read_ack,
    wait_for_ack,
)
from dream.observability import EventSink


def test_submit_then_drain_round_trips(tmp_path: Path) -> None:
    inbox = CommandInbox(tmp_path / "inbox")
    first = SubmitTaskCommand(intent="task one")
    second = StatusCommand()
    inbox.submit(first)
    inbox.submit(second)

    drained = inbox.drain()
    assert [c.id for c in drained] == [first.id, second.id]
    # Files are consumed.
    assert inbox.drain() == []


def test_drain_skips_and_removes_corrupt_files(tmp_path: Path) -> None:
    inbox_dir = tmp_path / "inbox"
    inbox = CommandInbox(inbox_dir)
    good = SubmitTaskCommand(intent="ok")
    inbox.submit(good)
    (inbox_dir / "0000000000.000000_bad.json").write_text("{nope", encoding="utf-8")

    drained = inbox.drain()
    assert [c.id for c in drained] == [good.id]
    assert list(inbox_dir.glob("*.json")) == []


def test_drain_on_missing_dir_is_empty(tmp_path: Path) -> None:
    assert CommandInbox(tmp_path / "nowhere").drain() == []


def test_ack_emit_and_read_back(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    sink = EventSink(events)
    ack = Ack(
        status="ok",
        summary="2 loops running",
        next_actions=("submit a task",),
        artifacts=(str(events),),
    )
    ack.emit(sink, command_id="cmd-1")

    found = read_ack(events, command_id="cmd-1")
    assert found is not None
    assert found.status == "ok"
    assert found.summary == "2 loops running"
    assert found.next_actions == ("submit a task",)


def test_read_ack_returns_none_when_absent(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    EventSink(events).emit("runtime.started")
    assert read_ack(events, command_id="cmd-x") is None


def test_wait_for_ack_times_out(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    EventSink(events)
    found = wait_for_ack(
        events, command_id="cmd-y", timeout_seconds=0.1, poll_seconds=0.02
    )
    assert found is None
