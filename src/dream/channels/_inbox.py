"""Command inbox — the runtime's inbound drop-dir (spec 15 P2 §1).

One JSON file per command at ``{ts:.6f}_{id}.json``; atomic writes via
``dream.utils.fs.atomic_write_text`` so a polling reader never sees a
partial command. Corrupt files are removed on drain (and surfaced to the
caller) so a bad write can't wedge the channel forever.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from dream.channels._commands import Command, command_from_dict
from dream.utils.fs import is_json_drop_file, save_json_file, try_load_json_file

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
        save_json_file(dest, command.to_dict(), trailing_newline=False)
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
            if not is_json_drop_file(path):
                continue
            command = try_load_json_file(path, command_from_dict)
            if command is not None:
                commands.append(command)
            with contextlib.suppress(OSError):
                path.unlink()
        commands.sort(key=lambda c: c.timestamp)
        return commands


