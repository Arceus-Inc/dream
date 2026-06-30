"""File-based mailbox bus between a leader and its worker subagents.

Pinned by spec 10 §"Worker notification": each message is a single JSON
file under ``<worktree>/.harness/swarm/{leader}/inbox/{ts:.6f}_{id}.json``.
Writes are atomic via ``dream.utils.fs.atomic_write_text`` (temp + fsync +
``os.replace``), so a polling reader never sees a partial message.

Message types are pinned to the set spec 10 documents — adding a new type
is a spec change, not a library detail.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from dream.utils.fs import is_json_drop_file, save_json_file, try_load_json_file

__all__ = [
    "Mailbox",
    "MailboxMessage",
    "MessageType",
    "make_permission_request",
    "make_permission_response",
    "make_shutdown",
    "make_task_notification",
    "make_user_message",
]


MessageType = Literal[
    "user_message",
    "permission_request",
    "permission_response",
    "task_notification",
    "shutdown",
]

_VALID_MESSAGE_TYPES: frozenset[str] = frozenset(get_args(MessageType))

_VALID_TASK_STATUSES: frozenset[str] = frozenset({"completed", "failed", "killed"})


@dataclass
class MailboxMessage:
    """A single message in flight between a leader and a worker."""

    id: str
    type: str  # narrowed to MessageType at construction by from_dict/factories
    sender: str
    recipient: str
    # Shape depends on ``type`` (see the make_* factories):
    #   user_message        -> {"content": str}
    #   task_notification   -> {"task_id": str, "status": str, "summary": str,
    #                           "result"?: str, "usage"?: dict}
    #   permission_request  -> {"request_id": str, "tool_name": str,
    #                           "tool_input": dict, "description": str}
    #   permission_response -> {"request_id": str, "allowed": bool,
    #                           "reason": str, "allow_once": bool}
    #   shutdown            -> {}
    payload: dict[str, Any]
    timestamp: float

    def __post_init__(self) -> None:
        if self.type not in _VALID_MESSAGE_TYPES:
            raise ValueError(
                f"unknown MailboxMessage type {self.type!r}; "
                f"expected one of {sorted(_VALID_MESSAGE_TYPES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MailboxMessage:
        return cls(
            id=data["id"],
            type=data["type"],
            sender=data["sender"],
            recipient=data["recipient"],
            payload=dict(data.get("payload") or {}),
            timestamp=float(data["timestamp"]),
        )


# --- factory helpers (one per documented type) ---------------------------


def _new_id() -> str:
    return uuid.uuid4().hex


def make_user_message(*, sender: str, recipient: str, content: str) -> MailboxMessage:
    return MailboxMessage(
        id=_new_id(),
        type="user_message",
        sender=sender,
        recipient=recipient,
        payload={"content": content},
        timestamp=time.time(),
    )


def make_task_notification(
    *,
    sender: str,
    recipient: str,
    task_id: str,
    status: str,
    summary: str,
    result: str | None = None,
    usage: dict[str, Any] | None = None,
) -> MailboxMessage:
    """Worker → leader completion notification.

    Spec 10 §"Worker notification": shape is ``{task_id, status, summary,
    result?, usage?}``; ``status`` is one of completed/failed/killed.
    """
    if status not in _VALID_TASK_STATUSES:
        raise ValueError(
            f"task_notification status {status!r} not in {sorted(_VALID_TASK_STATUSES)}"
        )
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "summary": summary,
    }
    if result is not None:
        payload["result"] = result
    if usage is not None:
        payload["usage"] = dict(usage)
    return MailboxMessage(
        id=_new_id(),
        type="task_notification",
        sender=sender,
        recipient=recipient,
        payload=payload,
        timestamp=time.time(),
    )


def make_permission_request(
    *,
    sender: str,
    recipient: str,
    request_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    description: str = "",
) -> MailboxMessage:
    return MailboxMessage(
        id=_new_id(),
        type="permission_request",
        sender=sender,
        recipient=recipient,
        payload={
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": dict(tool_input),
            "description": description,
        },
        timestamp=time.time(),
    )


def make_permission_response(
    *,
    sender: str,
    recipient: str,
    request_id: str,
    allowed: bool,
    reason: str = "",
    allow_once: bool = False,
) -> MailboxMessage:
    return MailboxMessage(
        id=_new_id(),
        type="permission_response",
        sender=sender,
        recipient=recipient,
        payload={
            "request_id": request_id,
            "allowed": bool(allowed),
            "reason": reason,
            "allow_once": bool(allow_once),
        },
        timestamp=time.time(),
    )


def make_shutdown(*, sender: str, recipient: str) -> MailboxMessage:
    return MailboxMessage(
        id=_new_id(),
        type="shutdown",
        sender=sender,
        recipient=recipient,
        payload={},
        timestamp=time.time(),
    )


# --- Mailbox ------------------------------------------------------------


@dataclass
class Mailbox:
    """File-based bus for one leader's inbox directory.

    The inbox is a flat directory of ``{ts:.6f}_{message_id}.json`` files.
    The leading timestamp keeps filename order ≡ wall-clock order, so a
    naive ``sorted()`` is a correct delivery order. Concurrent writers are
    safe because each message has its own unique filename (uuid4) and the
    write itself goes through the atomic helper.
    """

    inbox_dir: Path

    def __post_init__(self) -> None:
        self.inbox_dir = Path(self.inbox_dir)

    # write -----------------------------------------------------------

    def write(self, message: MailboxMessage) -> Path:
        """Atomically write ``message`` to the inbox; return the final path."""
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{message.timestamp:.6f}_{message.id}.json"
        dest = self.inbox_dir / filename
        save_json_file(dest, message.to_dict(), trailing_newline=False)
        return dest

    # read ------------------------------------------------------------

    def _scan(self) -> list[tuple[Path, MailboxMessage | None]]:
        """Return ``(path, message)`` for every message file, sorted by name.

        ``message`` is ``None`` for a file that failed to load (corrupt /
        unreadable); callers decide whether to surface or remove those.
        """
        if not self.inbox_dir.is_dir():
            return []
        return [
            (path, try_load_json_file(path, MailboxMessage.from_dict))
            for path in sorted(self.inbox_dir.iterdir())
            if is_json_drop_file(path)
        ]

    def read_all(self) -> list[MailboxMessage]:
        """Return every well-formed message, oldest-first by timestamp."""
        messages = [msg for _, msg in self._scan() if msg is not None]
        # Sort by the timestamp field, not just the filename, so a future
        # caller that bypassed the filename convention still gets ordered
        # delivery.
        messages.sort(key=lambda m: m.timestamp)
        return messages

    def drain(self) -> list[MailboxMessage]:
        """Read all messages then delete the underlying files."""
        scanned = self._scan()
        messages = [msg for _, msg in scanned if msg is not None]
        messages.sort(key=lambda m: m.timestamp)
        for path, _ in scanned:  # remove corrupted files too, so they don't pile up
            # Best-effort: a peer that already removed it is fine.
            with contextlib.suppress(OSError):
                path.unlink()
        return messages



