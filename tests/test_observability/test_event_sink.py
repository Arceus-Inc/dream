"""EventSink — the runtime's outbound JSONL stream (spec 15 P2 §2).

Rotation keeps a long-running daemon's event file bounded: when the file
would exceed ``max_bytes`` the sink renames it to ``<name>.1`` (one
generation — the stream is observability, not the system of record) and
starts fresh. ``tail_events`` is the read side SDK consumers use.
"""

from __future__ import annotations

import json
from pathlib import Path

from dream.observability import EventSink, tail_events


def test_emit_appends_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    sink.emit("a", x=1)
    sink.emit("b", y=2)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["a", "b"]


def test_rotation_when_max_bytes_exceeded(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = EventSink(path, max_bytes=200)
    for n in range(50):
        sink.emit("tick", n=n)
    rotated = tmp_path / "events.jsonl.1"
    assert rotated.exists()
    assert path.stat().st_size <= 200 + 200  # current file stays bounded
    # No third generation: .1 is overwritten, not chained.
    assert not (tmp_path / "events.jsonl.2").exists()
    # The live file still ends with the newest event.
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["n"] == 49


def test_no_rotation_by_default(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = EventSink(path)
    for n in range(50):
        sink.emit("tick", n=n)
    assert not (tmp_path / "events.jsonl.1").exists()


def test_tail_events_reads_parsed_records(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = EventSink(path)
    sink.emit("a", x=1)
    sink.emit("b", y=2)
    records = list(tail_events(path))
    assert [r["type"] for r in records] == ["a", "b"]
    assert records[0]["x"] == 1


def test_tail_events_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = EventSink(path)
    sink.emit("a")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{torn line\n")
    sink.emit("b")
    assert [r["type"] for r in tail_events(path)] == ["a", "b"]


def test_tail_events_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(tail_events(tmp_path / "nope.jsonl")) == []


def test_tail_events_last_limits_output(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = EventSink(path)
    for n in range(10):
        sink.emit("tick", n=n)
    records = list(tail_events(path, last=3))
    assert [r["n"] for r in records] == [7, 8, 9]
