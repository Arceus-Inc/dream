"""JSONL event sink — append-only, one event per line (spec 15 P2 §2).

The runtime's outbound API: every event carries an ISO8601 ``ts``, the
writer ``pid``, and a ``type`` discriminator; the rest is type-specific
payload. ``tail_events`` is the matching read side.

Rotation (opt-in via ``max_bytes``) keeps a long-running daemon's file
bounded: the full file is renamed to ``<name>.1`` — exactly one prior
generation, because the stream is observability, not the system of
record (spec 00 invariant 2: durable state lives in git, not here).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dream.utils.fs import compact_json


class EventSink:
    """Thread-safe append-only JSONL writer with optional size rotation.

    A single lock serialises writes from the REPL loop, the runtime's
    supervised loops, and task-lifecycle listeners.
    """

    def __init__(self, path: Path, *, max_bytes: int | None = None) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file so ``watch`` can open it immediately.
        path.touch(exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        # Reserved fields are written *last* so a caller-supplied payload key
        # (type/ts/pid) can never clobber the sink's stable discriminator,
        # timestamp, or pid.
        record = {
            **payload,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "pid": os.getpid(),
            "type": event_type,
        }
        line = compact_json(record)
        with self._lock:
            self._rotate_if_needed(len(line) + 1)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self._max_bytes is None:
            return
        try:
            current = self._path.stat().st_size
        except OSError:
            return
        if current + incoming_bytes <= self._max_bytes:
            return
        # ``os.replace`` clobbers an existing .1 — one prior generation only.
        os.replace(self._path, self._path.with_name(self._path.name + ".1"))
        self._path.touch()

    def callback(self, event: dict[str, Any]) -> None:
        """Adapter for :class:`FailoverPolicy.on_event` (already a dict)."""
        # ``get`` + a fresh payload dict, never ``pop``: mutating the caller's
        # dict would strip ``type`` from it and break any second listener.
        event_type = str(event.get("type", "substrate.unknown"))
        payload = {k: v for k, v in event.items() if k != "type"}
        self.emit(event_type, **payload)


def tail_events(path: Path, *, last: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield parsed event records from a JSONL stream, oldest-first.

    Torn or corrupt lines (a crash mid-write) are skipped — the stream
    must stay readable after any failure. ``last=N`` returns only the
    newest N records.
    """
    if not path.exists():
        return
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    if last is not None:
        records = records[-last:]
    yield from records


__all__ = ["EventSink", "tail_events"]
