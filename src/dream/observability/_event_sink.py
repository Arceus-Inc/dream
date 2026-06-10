"""JSONL event sink — append-only, one event per line.

Same shape the eventual Stage 03 orchestrator is expected to emit, so the
``watch`` view doesn't need a rewrite when the orchestrator lands. Every
event carries an ISO8601 ``ts`` and a ``type`` discriminator; the rest is
type-specific payload.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventSink:
    """Thread-safe append-only JSONL writer.

    A single file lock serialises writes from the REPL loop and the
    failover policy callback, both of which run on the same thread today
    but may not in Stage 03.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
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
        line = json.dumps(record, default=str, separators=(",", ":"))
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    def callback(self, event: dict[str, Any]) -> None:
        """Adapter for :class:`FailoverPolicy.on_event` (already a dict)."""
        # ``get`` + a fresh payload dict, never ``pop``: mutating the caller's
        # dict would strip ``type`` from it and break any second listener.
        event_type = str(event.get("type", "substrate.unknown"))
        payload = {k: v for k, v in event.items() if k != "type"}
        self.emit(event_type, **payload)


__all__ = ["EventSink"]
