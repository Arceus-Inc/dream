"""Tail a JSONL event file with mild pretty-printing.

Two-terminal flow: the chat REPL writes events; this watcher reads them.
No log rotation, no follow-from-offset — meant for dev observability, not
a production tail.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# ANSI colour helpers — gated on a TTY check so piping into a file stays clean.
_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GREEN = "\x1b[32m"
_CYAN = "\x1b[36m"
_MAGENTA = "\x1b[35m"


def _colour_for(event_type: str) -> str:
    if event_type.startswith("substrate.failover"):
        return _MAGENTA
    if event_type.startswith("substrate.health.recovered"):
        return _GREEN
    if event_type.startswith("substrate.health.degraded"):
        return _YELLOW
    if event_type == "turn.completed":
        return _GREEN
    if event_type == "turn.attempt_failed":
        return _RED
    if event_type == "context.compaction.completed":
        return _CYAN
    if event_type == "context.compaction.triggered":
        return _YELLOW
    if event_type == "session.error" or event_type == "session.turn_failed":
        return _RED
    if event_type == "session.turn_complete":
        return _GREEN
    if event_type.startswith("session.repl."):
        return _CYAN
    if event_type.startswith("session."):
        return _DIM
    if event_type.startswith("repl."):
        return _CYAN
    return ""


def _format(record: dict[str, object], use_colour: bool) -> str:
    ts = str(record.get("ts", ""))
    event_type = str(record.get("type", "unknown"))
    rest = {k: v for k, v in record.items() if k not in {"ts", "type", "pid"}}
    payload = " ".join(f"{k}={v}" for k, v in rest.items())
    if use_colour:
        colour = _colour_for(event_type)
        return f"{_DIM}{ts}{_RESET} {colour}{event_type}{_RESET} {payload}".rstrip()
    return f"{ts} {event_type} {payload}".rstrip()


def run_watch(path: Path, *, from_start: bool = False, use_colour: bool | None = None) -> int:
    """Tail `path` line by line, formatting each JSON record."""
    if use_colour is None:
        import sys

        use_colour = sys.stdout.isatty()

    # Wait for the file to exist so the watcher can be started before the chat.
    while not path.exists():
        print(f"[waiting for {path}]")
        time.sleep(1.0)

    with path.open("r", encoding="utf-8") as fh:
        if not from_start:
            fh.seek(0, 2)  # SEEK_END
        buf = ""
        while True:
            chunk = fh.read()
            if not chunk:
                time.sleep(0.25)
                continue
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[malformed line] {line[:200]}")
                    continue
                print(_format(record, use_colour))


__all__ = ["run_watch"]
