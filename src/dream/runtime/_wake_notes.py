"""Wake notes — the durable queue between cron firings and the next wake.

The timed-note pattern (OpenClaw ``wakeMode: next-heartbeat``): a cron
manifest with ``target = "next-wake"`` doesn't spawn work — its firing
drops a note here, and the wake scheduler drains pending notes into the
next heartbeat turn as extra context, with ``CronWake`` as the source.

Same drop-dir discipline as the command inbox: one atomic JSON file per
note, sortable names, corrupt files removed on drain so a bad write can
never wedge the queue.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from dream.utils.fs import atomic_write_text, is_json_drop_file

__all__ = ["WakeNote", "WakeNoteStore"]


@dataclass(frozen=True)
class WakeNote:
    """One queued nudge for the next wake."""

    text: str
    source: str
    created_at: float


@dataclass
class WakeNoteStore:
    """File-based note queue for one agent's wake scheduler."""

    notes_dir: Path

    def __post_init__(self) -> None:
        self.notes_dir = Path(self.notes_dir)

    def add(self, text: str, *, source: str) -> Path:
        """Atomically queue one note; return the file path."""
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        note = WakeNote(text=text, source=source, created_at=time.time())
        dest = self.notes_dir / f"{note.created_at:.6f}_{uuid.uuid4().hex}.json"
        atomic_write_text(
            dest,
            json.dumps(
                {"text": note.text, "source": note.source, "created_at": note.created_at}
            ),
        )
        return dest

    def pending(self) -> int:
        """How many notes are queued (a peek — consumes nothing)."""
        if not self.notes_dir.is_dir():
            return 0
        return sum(1 for path in self.notes_dir.iterdir() if is_json_drop_file(path))

    def drain(self) -> list[WakeNote]:
        """Read + delete every pending note, oldest-first.

        Corrupt files are deleted too — they'd re-fail on every wake for
        the life of the process otherwise.
        """
        if not self.notes_dir.is_dir():
            return []
        notes: list[WakeNote] = []
        for path in sorted(self.notes_dir.iterdir()):
            if not is_json_drop_file(path):
                continue
            note = _try_load(path)
            if note is not None:
                notes.append(note)
            with contextlib.suppress(OSError):
                path.unlink()
        notes.sort(key=lambda n: n.created_at)
        return notes


def _try_load(path: Path) -> WakeNote | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("text"):
        return None
    return WakeNote(
        text=str(data["text"]),
        source=str(data.get("source") or "unknown"),
        created_at=float(data.get("created_at") or 0.0),
    )
