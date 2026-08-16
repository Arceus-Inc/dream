"""Immutable read model for one session's existing JSONL trace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.observability._events import TraceEvent
from dream.observability._query import read_events


@dataclass(frozen=True)
class RunTrace:
    """Typed, immutable aggregate of trace events for one session.

    ``read`` delegates parsing and malformed-tail tolerance to the established
    JSONL reader; it adds only the session identity validation a control plane
    needs when associating a trace with a saved session handle. Each event's
    ``attributes`` are a sealed ``FrozenJsonObject``.
    """

    session_id: str
    events: tuple[TraceEvent, ...]

    @classmethod
    def read(cls, *, session_id: str, path: Path) -> RunTrace:
        """Read a session's trace without introducing another trace store."""
        parsed = read_events(path)
        for event in parsed:
            if event.session_id != session_id:
                raise ValueError(
                    f"trace event session {event.session_id!r} does not match {session_id!r}"
                )
        return cls(session_id=session_id, events=tuple(parsed))


__all__ = ["RunTrace"]
