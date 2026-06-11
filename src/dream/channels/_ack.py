"""Command acknowledgements — the observation contract (spec 15 P2 §3).

Every command ack carries ``status / summary / next_actions /
artifacts``, the same grammar as ToolResult metadata, emitted on the
runtime event stream as ``runtime.command.ack``. Senders correlate by
``command_id``; ``read_ack`` / ``wait_for_ack`` are the read side the
CLI (and SDK consumers) use.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dream.observability import EventSink

__all__ = ["ACK_EVENT_TYPE", "Ack", "read_ack", "wait_for_ack"]

ACK_EVENT_TYPE = "runtime.command.ack"

AckStatus = Literal["ok", "error", "rejected"]


@dataclass(frozen=True)
class Ack:
    """One command's reply: what happened, what to do next, what to read."""

    status: AckStatus
    summary: str
    next_actions: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[str, ...] = field(default_factory=tuple)

    def emit(self, sink: EventSink, *, command_id: str) -> dict[str, Any]:
        return sink.emit(
            ACK_EVENT_TYPE,
            command_id=command_id,
            status=self.status,
            summary=self.summary,
            next_actions=list(self.next_actions),
            artifacts=list(self.artifacts),
        )


def read_ack(events_path: Path, *, command_id: str) -> Ack | None:
    """Scan the event stream for ``command_id``'s ack; newest wins."""
    if not events_path.exists():
        return None
    found: Ack | None = None
    with events_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                record.get("type") == ACK_EVENT_TYPE
                and record.get("command_id") == command_id
            ):
                found = Ack(
                    status=record.get("status", "error"),
                    summary=str(record.get("summary", "")),
                    next_actions=tuple(record.get("next_actions") or ()),
                    artifacts=tuple(record.get("artifacts") or ()),
                )
    return found


def wait_for_ack(
    events_path: Path,
    *,
    command_id: str,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.2,
) -> Ack | None:
    """Block until the ack appears or the timeout elapses (CLI read side)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        ack = read_ack(events_path, command_id=command_id)
        if ack is not None:
            return ack
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)
