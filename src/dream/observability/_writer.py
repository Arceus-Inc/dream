"""TraceWriter — append-only JSONL sink for trace events (Spec 12a).

Per-task, under ``.dream/sidecars/{task-id}/logs/trace.jsonl`` (repo-as-record,
inside the worktree). One line per event. The trace is durable — compaction
(#04) never touches it.

Each write opens the file in append mode and closes it, so the writer holds no
persistent file descriptor: there is nothing to leak across long-running,
many-session processes and no lifecycle to manage (the per-session REPL creates
one per session). Trace volume is low relative to model/tool latency, so the
open-cost is negligible.
"""

from __future__ import annotations

import threading
from pathlib import Path

from dream.observability._events import TraceEvent, to_jsonl_line


class TraceWriter:
    """Append-only writer for :class:`TraceEvent` lines (no persistent handle)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: TraceEvent) -> None:
        line = to_jsonl_line(event) + "\n"
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)


__all__ = ["TraceWriter"]
