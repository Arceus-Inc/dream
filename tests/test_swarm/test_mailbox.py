"""Spec 10 slice B — ``Mailbox`` + ``MailboxMessage`` (file protocol).

Pinned semantics:

- One JSON file per message, named ``{ts:.6f}_{message_id}.json``.
- Writes are atomic (no partial reads ever observable to a polling reader).
- Reads are sorted by timestamp (oldest first); drain returns + deletes.
- Message ``type`` is fixed to the spec 10 set; other values rejected.
- Factory helpers for each documented type stamp ``timestamp`` + ``id``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from dream.swarm._mailbox import (
    Mailbox,
    MailboxMessage,
    make_permission_request,
    make_permission_response,
    make_shutdown,
    make_task_notification,
    make_user_message,
)
from dream.swarm._paths import leader_inbox_dir


# --- MailboxMessage shape ----------------------------------------------------


def test_message_round_trips_through_dict() -> None:
    m = MailboxMessage(
        id="abc",
        type="user_message",
        sender="planner",
        recipient="generator",
        payload={"content": "hi"},
        timestamp=1234.5,
    )
    assert MailboxMessage.from_dict(m.to_dict()) == m


def test_message_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        MailboxMessage.from_dict(
            {
                "id": "a",
                "type": "ceo_directive",  # not a spec 10 type
                "sender": "x",
                "recipient": "y",
                "payload": {},
                "timestamp": 1.0,
            }
        )


# --- Mailbox.write -----------------------------------------------------------


def test_write_creates_directory_on_demand(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    assert not inbox.exists()
    mb.write(make_user_message(sender="a", recipient="b", content="hi"))
    assert inbox.is_dir()


def test_write_uses_atomic_temp_then_rename(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    mb.write(make_user_message(sender="a", recipient="b", content="hi"))

    # No ``.tmp.<hex>`` orphans (atomic_write_bytes cleans up on success).
    leftovers = [p for p in inbox.iterdir() if ".tmp." in p.name]
    assert leftovers == []
    # Exactly one JSON file landed.
    files = sorted(p for p in inbox.iterdir() if p.suffix == ".json")
    assert len(files) == 1


def test_write_filename_starts_with_timestamp_for_sort_stability(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    a = make_user_message(sender="a", recipient="b", content="one")
    a.timestamp = 1.0
    a.id = "aaa"
    b = make_user_message(sender="a", recipient="b", content="two")
    b.timestamp = 2.0
    b.id = "bbb"
    mb.write(a)
    mb.write(b)

    names = sorted(p.name for p in inbox.iterdir() if p.suffix == ".json")
    assert names[0].startswith("1.")
    assert names[1].startswith("2.")
    # The ID is in the filename so we can find one message by id without parsing.
    assert "aaa" in names[0]
    assert "bbb" in names[1]


# --- Mailbox.read_all / drain -----------------------------------------------


def test_read_all_returns_messages_sorted_by_timestamp(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    # Insert in reverse to prove read_all sorts (not just relies on glob order).
    later = make_user_message(sender="a", recipient="b", content="later")
    later.timestamp = 10.0
    earlier = make_user_message(sender="a", recipient="b", content="earlier")
    earlier.timestamp = 1.0
    mb.write(later)
    mb.write(earlier)

    out = mb.read_all()
    assert [m.payload["content"] for m in out] == ["earlier", "later"]


def test_read_all_skips_tmp_files_and_dotfiles(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    inbox.mkdir(parents=True)
    # A partially-written file in the wild — readers must NOT see it.
    (inbox / "0.5_partial.json.tmp.deadbeef").write_text("not valid", encoding="utf-8")
    # A dotfile (e.g. a future lockfile).
    (inbox / ".write_lock").write_text("lock", encoding="utf-8")

    mb = Mailbox(inbox)
    mb.write(make_user_message(sender="a", recipient="b", content="real"))

    out = mb.read_all()
    assert len(out) == 1
    assert out[0].payload["content"] == "real"


def test_read_all_skips_corrupted_json(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    inbox.mkdir(parents=True)
    (inbox / "0.5_bad.json").write_text("{not json", encoding="utf-8")

    mb = Mailbox(inbox)
    mb.write(make_user_message(sender="a", recipient="b", content="real"))

    out = mb.read_all()
    assert len(out) == 1
    assert out[0].payload["content"] == "real"


def test_drain_returns_then_removes(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    mb.write(make_user_message(sender="a", recipient="b", content="hi"))
    mb.write(make_user_message(sender="a", recipient="b", content="bye"))

    out = mb.drain()
    assert len(out) == 2
    # After drain, the inbox is empty of .json messages.
    leftover = [p for p in inbox.iterdir() if p.suffix == ".json"]
    assert leftover == []


def test_drain_idempotent_when_empty(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    assert mb.drain() == []
    assert mb.drain() == []


# --- concurrency: no partial reads, no double-delivery ----------------------


def test_concurrent_writers_no_corruption(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)

    def _writer(n: int) -> None:
        for i in range(20):
            msg = make_user_message(sender=f"w{n}", recipient="b", content=f"{n}-{i}")
            mb.write(msg)

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out = mb.read_all()
    assert len(out) == 4 * 20
    # Every payload is intact (no torn JSON).
    contents = sorted(m.payload["content"] for m in out)
    expected = sorted(f"{n}-{i}" for n in range(4) for i in range(20))
    assert contents == expected


# --- factory helpers -------------------------------------------------------


def test_user_message_factory_shape() -> None:
    m = make_user_message(sender="a", recipient="b", content="hi")
    assert m.type == "user_message"
    assert m.payload == {"content": "hi"}
    assert m.sender == "a" and m.recipient == "b"
    assert m.id  # non-empty
    assert m.timestamp > 0


def test_task_notification_factory_carries_spec_fields() -> None:
    # Spec 10 §"Worker notification": task_id, status, summary, optional result, usage.
    m = make_task_notification(
        sender="generator",
        recipient="runner",
        task_id="T1",
        status="completed",
        summary="all done",
        result="diff at sidecar://...",
        usage={"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001},
    )
    assert m.type == "task_notification"
    assert m.payload["task_id"] == "T1"
    assert m.payload["status"] == "completed"
    assert m.payload["summary"] == "all done"
    assert m.payload["result"] == "diff at sidecar://..."
    assert m.payload["usage"]["input_tokens"] == 100


def test_task_notification_omits_optional_fields_when_absent() -> None:
    m = make_task_notification(
        sender="generator",
        recipient="runner",
        task_id="T1",
        status="failed",
        summary="boom",
    )
    assert "result" not in m.payload
    assert "usage" not in m.payload


def test_task_notification_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        make_task_notification(
            sender="generator",
            recipient="runner",
            task_id="T1",
            status="halfway",  # not completed/failed/killed
            summary="x",
        )


def test_permission_request_factory_carries_required_fields() -> None:
    m = make_permission_request(
        sender="generator",
        recipient="planner",
        request_id="perm-1",
        tool_name="file_write",
        tool_input={"path": "secrets.txt", "content": "x"},
        description="write outside allowed paths",
    )
    assert m.type == "permission_request"
    assert m.payload["request_id"] == "perm-1"
    assert m.payload["tool_name"] == "file_write"
    assert m.payload["tool_input"] == {"path": "secrets.txt", "content": "x"}


def test_permission_response_factory_carries_decision() -> None:
    m = make_permission_response(
        sender="planner",
        recipient="generator",
        request_id="perm-1",
        allowed=True,
        reason="ok this once",
    )
    assert m.type == "permission_response"
    assert m.payload["request_id"] == "perm-1"
    assert m.payload["allowed"] is True
    assert m.payload["reason"] == "ok this once"


def test_shutdown_factory() -> None:
    m = make_shutdown(sender="runner", recipient="generator")
    assert m.type == "shutdown"
    assert m.payload == {}


# --- on-disk format is stable JSON (an operator can read it) ---------------


def test_on_disk_payload_is_human_readable_json(tmp_path: Path) -> None:
    inbox = leader_inbox_dir(tmp_path, "planner")
    mb = Mailbox(inbox)
    mb.write(make_user_message(sender="a", recipient="b", content="hi"))

    [path] = [p for p in inbox.iterdir() if p.suffix == ".json"]
    data = json.loads(path.read_text(encoding="utf-8"))
    # The fields the spec promises operators they can see.
    for key in ("id", "type", "sender", "recipient", "payload", "timestamp"):
        assert key in data


# --- timestamp monotonicity helper -----------------------------------------


def test_factory_timestamps_are_close_to_wall_clock() -> None:
    before = time.time()
    m = make_user_message(sender="a", recipient="b", content="x")
    after = time.time()
    assert before <= m.timestamp <= after
