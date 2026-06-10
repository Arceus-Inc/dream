"""Runtime control plane — commands in, events out (spec 15 P2).

The inbound channel is a drop-dir of JSON command files (atomic writes,
the ``swarm/_mailbox`` pattern) at ``.dream/runtime/inbox/``. The
outbound channel is the runtime events JSONL at
``.dream/runtime/events.jsonl``. Every command is acked on the event
stream as ``runtime.command.ack`` carrying the observation contract:
``status / summary / next_actions / artifacts`` — one grammar for agents
and humans alike.

Commands are micro-tools with strict schemas (submit_task / cancel /
status / wake) — no catch-all "do(...)" door. A Unix-socket or HTTP
gateway is a later adapter behind the same command types.
"""

from __future__ import annotations

from dream.channels._ack import Ack, read_ack, wait_for_ack
from dream.channels._commands import (
    CancelCommand,
    Command,
    StatusCommand,
    SubmitTaskCommand,
    WakeCommand,
    command_from_dict,
)
from dream.channels._inbox import CommandInbox

__all__ = [
    "Ack",
    "CancelCommand",
    "Command",
    "CommandInbox",
    "StatusCommand",
    "SubmitTaskCommand",
    "WakeCommand",
    "command_from_dict",
    "read_ack",
    "wait_for_ack",
]
