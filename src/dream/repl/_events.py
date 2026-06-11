"""Back-compat shim — ``EventSink`` moved to ``dream.observability``.

The sink started life as a REPL-private JSONL writer; the long-running
runtime (spec 15 P1) made it the process-wide event stream, so it now
lives with the rest of observability. Importers inside ``dream.repl``
and existing tests keep working through this re-export.
"""

from __future__ import annotations

from dream.observability._event_sink import EventSink

__all__ = ["EventSink"]
