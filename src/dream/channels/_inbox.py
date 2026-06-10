"""Command inbox — the runtime's inbound drop-dir (spec 15 P2 §1).

One JSON file per command at ``{ts:.6f}_{id}.json``; atomic writes via
``dream.utils.fs.atomic_write_text`` so a polling reader never sees a
partial command. Corrupt files are removed on drain (and surfaced to the
caller) so a bad write can't wedge the channel forever.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from dream.channels._commands import Command, command_from_dict
from dream.utils.fs import atomic_write_text

__all__ = ["CommandInbox"]


@dataclass
class CommandInbox:
    """File-based command queue for one runtime's inbox directory."""

    inbox_dir: Path

    def __post_init__(self) -> None:
        self.inbox_dir = Path(self.inbox_dir)

    def submit(self, command: Command) -> Path:
        """Atomically write ``command`` into the inbox; return the file path."""
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        dest = self.inbox_dir / f"{command.timestamp:.6f}_{command.id}.json"
        atomic_write_text(dest, json.dumps(command.to_dict(), indent=2))
        return dest

    def drain(self) -> list[Command]:
        """Read + delete every pending command, oldest-first.

        Corrupt or off-schema files are deleted too — the sender gets no
        ack (their timeout is the signal); leaving them would re-fail on
        every poll for the life of the process.
        """
        if not self.inbox_dir.is_dir():
            return []
        commands: list[Command] = []
        for path in sorted(self.inbox_dir.iterdir()):
            if not _is_command_file(path):
                continue
            command = _try_load(path)
            if command is not None:
                commands.append(command)
            with contextlib.suppress(OSError):
                path.unlink()
        commands.sort(key=lambda c: c.timestamp)
        return commands


def _is_command_file(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or path.suffix != ".json" or ".tmp." in name:
        return False
    return path.is_file()


def _try_load(path: Path) -> Command | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return command_from_dict(data)
    except (KeyError, ValueError, TypeError):
        return None
