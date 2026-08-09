"""Immutable read model for one session's existing JSONL trace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream._immutable_json import FrozenJsonObject
from dream.observability._events import TraceEvent
from dream.observability._query import read_events


@dataclass(frozen=True)
class RunTrace:
    """Typed, immutable aggregate of trace events for one session.

    ``read`` delegates parsing and malformed-tail tolerance to the established
    JSONL reader; it adds only the session identity validation a control plane
    needs when associating a trace with a saved session handle.
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
        events = tuple(
            TraceEvent(
                ts=event.ts,
                session_id=event.session_id,
                task_id=event.task_id,
                event_type=event.event_type,
                span_id=event.span_id,
                parent_span_id=event.parent_span_id,
                attributes=FrozenJsonObject.capture(event.attributes),
            )
            for event in parsed
        )
        return cls(session_id=session_id, events=events)


__all__ = ["RunTrace"]
