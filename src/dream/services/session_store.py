"""Durable session snapshots on disk (PR1: save/resume across process boundaries).

``FileSessionStore`` writes typed ``SessionSnapshot`` JSON under a root
directory (typically ``DreamPaths.sessions_dir``). Snapshots carry the
transcript, extracted tool-call records, cost counters, and session
metadata needed to restore a ``Session`` without the in-memory engine.

This store is the harness's own transcript of record — the equivalent of a
coding CLI's on-disk session log. A control plane driving the harness across
process boundaries is expected to persist only the returned
:class:`SessionHandle` (session id + working dir + usage), not a second copy
of the transcript, and to resume through ``Harness.resume_session``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeGuard

from dream._immutable_json import FrozenJsonObject
from dream.api.structured import JsonValue
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    ImageBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.errors import SessionResumeError
from dream.utils.fs import atomic_write_text

# Bumped to 2 when snapshots started recording ``working_dir``. A version-1 file
# has no directory to check, so resuming one would silently skip the binding
# that keeps a transcript in the workspace it was written for; reading it as a
# foreign schema refuses it instead.
SCHEMA_VERSION = 2

__all__ = [
    "SCHEMA_VERSION",
    "ContentBlockRecord",
    "ConversationMessageRecord",
    "FileSessionStore",
    "SessionCostFields",
    "SessionCostSnapshot",
    "SessionHandle",
    "SessionSnapshot",
    "ToolCallRecord",
    "checked_session_id",
    "cost_snapshot_from_fields",
    "extract_tool_calls",
    "is_json_value",
    "json_dict_from_mapping",
    "message_to_record",
    "messages_from_records",
    "record_to_message",
]

RoleLiteral = Literal["user", "assistant"]
BlockKind = Literal["text", "image", "tool_use", "tool_result"]


@dataclass(frozen=True)
class TextBlockRecord:
    kind: Literal["text"]
    text: str


@dataclass(frozen=True)
class ImageBlockRecord:
    kind: Literal["image"]
    media_type: str
    data: str


@dataclass(frozen=True)
class ToolUseBlockRecord:
    kind: Literal["tool_use"]
    id: str
    name: str
    input: FrozenJsonObject


@dataclass(frozen=True)
class ToolResultBlockRecord:
    kind: Literal["tool_result"]
    tool_use_id: str
    content: str
    is_error: bool


ContentBlockRecord = TextBlockRecord | ImageBlockRecord | ToolUseBlockRecord | ToolResultBlockRecord


@dataclass(frozen=True)
class ConversationMessageRecord:
    role: RoleLiteral
    content: tuple[ContentBlockRecord, ...]


@dataclass(frozen=True)
class SessionCostSnapshot:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ToolCallRecord:
    tool_use_id: str
    tool_name: str
    input: FrozenJsonObject
    result_content: str | None
    is_error: bool | None


@dataclass(frozen=True)
class SessionSnapshot:
    """Immutable durable state of one provider-independent session.

    The file-store codec remains the source of truth for serialization; this
    value object gives callers a stable point-in-time view without exposing
    mutable transcript collections.
    """

    schema_version: int
    session_id: str
    model: str
    system_prompt: str | None
    cost: SessionCostSnapshot
    messages: tuple[ConversationMessageRecord, ...]
    tool_calls: tuple[ToolCallRecord, ...]
    saved_at: datetime
    # Keyword-only from here down. Each of these is something the harness
    # learned to persist after the fact, and the next one will land beside
    # them; positional callers would then bind an old value to a new field.
    max_turns: int | None = field(default=None, kw_only=True)
    # The directory the session did its work in. A resume into a different
    # working directory replays a transcript about other files, so the harness
    # refuses it unless the caller opts in. ``None`` for engine-less sessions.
    working_dir: str | None = field(default=None, kw_only=True)
    metadata: FrozenJsonObject = field(default_factory=FrozenJsonObject, kw_only=True)


@dataclass(frozen=True)
class SessionHandle:
    """What a caller persists to resume a session later.

    The durable pointer at the transcript the harness already owns — the
    control-plane row a scheduler keys by task. ``usage_delta`` covers only the
    work since the previous save so a caller can bill per run without
    differencing cumulative totals itself; ``usage_total`` is the session's
    running total.
    """

    session_id: str
    path: Path
    working_dir: str | None
    schema_version: int
    saved_at: datetime
    usage_delta: SessionCostSnapshot
    usage_total: SessionCostSnapshot


def checked_session_id(session_id: str) -> str:
    """Reject a session id that could escape the sessions root.

    ``:`` is rejected alongside the path separators because a session id also
    names a sidecar directory (it is the trace log's key), and the task-id
    validator guarding that root treats a colon as Windows drive and alternate
    -data-stream syntax. Catching it here means a caller-supplied scope fails
    where it is set rather than deep inside engine construction.
    """
    if (
        not session_id
        or session_id in {".", ".."}
        or "/" in session_id
        or "\\" in session_id
        or ":" in session_id
        or "\x00" in session_id
        or os.path.isabs(session_id)
    ):
        raise ValueError(f"unsafe session_id: {session_id!r}")
    return session_id


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and is_json_value(item) for key, item in value.items())
    return False


def json_dict_from_mapping(raw: Mapping[str, object]) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"JSON object key must be str, got {type(key).__name__}")
        out[key] = _json_value_from_object(value, key=key)
    return out


def _json_value_from_object(value: object, *, key: str) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value_from_object(item, key=key) for item in value]
    if isinstance(value, Mapping):
        return json_dict_from_mapping(value)
    raise ValueError(f"value for {key!r} is not a valid JsonValue")


def message_to_record(message: ConversationMessage) -> ConversationMessageRecord:
    blocks: list[ContentBlockRecord] = []
    for block in message.content:
        blocks.append(block_to_record(block))
    role: RoleLiteral = message.role
    return ConversationMessageRecord(role=role, content=tuple(blocks))


def block_to_record(block: ContentBlock) -> ContentBlockRecord:
    if isinstance(block, TextBlock):
        return TextBlockRecord(kind="text", text=block.text)
    if isinstance(block, ImageBlock):
        return ImageBlockRecord(kind="image", media_type=block.media_type, data=block.data)
    if isinstance(block, ToolUseBlock):
        return ToolUseBlockRecord(
            kind="tool_use",
            id=block.id,
            name=block.name,
            input=FrozenJsonObject.capture(block.input),
        )
    if isinstance(block, ToolResultBlock):
        return ToolResultBlockRecord(
            kind="tool_result",
            tool_use_id=block.tool_use_id,
            content=block.content,
            is_error=block.is_error,
        )
    raise TypeError(f"unsupported content block: {type(block).__name__}")


def record_to_message(record: ConversationMessageRecord) -> ConversationMessage:
    blocks: list[ContentBlock] = [record_to_block(b) for b in record.content]
    role: Role = record.role
    return ConversationMessage(role=role, content=blocks)


def record_to_block(record: ContentBlockRecord) -> ContentBlock:
    if isinstance(record, TextBlockRecord):
        return TextBlock(text=record.text)
    if isinstance(record, ImageBlockRecord):
        return ImageBlock(media_type=record.media_type, data=record.data)
    if isinstance(record, ToolUseBlockRecord):
        return ToolUseBlock(
            id=record.id,
            name=record.name,
            input=json_dict_from_mapping(record.input.thaw()),
        )
    if isinstance(record, ToolResultBlockRecord):
        return ToolResultBlock(
            tool_use_id=record.tool_use_id,
            content=record.content,
            is_error=record.is_error,
        )
    raise TypeError(f"unsupported content block record: {type(record).__name__}")


def extract_tool_calls(messages: Sequence[ConversationMessage]) -> list[ToolCallRecord]:
    """Pair each ``ToolUseBlock`` with its matching ``ToolResultBlock``."""
    results_by_id: dict[str, ToolResultBlock] = {}
    for message in messages:
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                results_by_id[block.tool_use_id] = block

    records: list[ToolCallRecord] = []
    for message in messages:
        for block in message.content:
            if not isinstance(block, ToolUseBlock):
                continue
            result = results_by_id.get(block.id)
            records.append(
                ToolCallRecord(
                    tool_use_id=block.id,
                    tool_name=block.name,
                    input=FrozenJsonObject.capture(block.input),
                    result_content=result.content if result is not None else None,
                    is_error=result.is_error if result is not None else None,
                )
            )
    return records


def snapshot_to_dict(snapshot: SessionSnapshot) -> dict[str, object]:
    return {
        "schema_version": snapshot.schema_version,
        "session_id": snapshot.session_id,
        "model": snapshot.model,
        "system_prompt": snapshot.system_prompt,
        "max_turns": snapshot.max_turns,
        "working_dir": snapshot.working_dir,
        "metadata": json_dict_from_mapping(snapshot.metadata.thaw()),
        "cost": {
            "input_tokens": snapshot.cost.input_tokens,
            "output_tokens": snapshot.cost.output_tokens,
            "cache_read_tokens": snapshot.cost.cache_read_tokens,
            "cache_write_tokens": snapshot.cost.cache_write_tokens,
            "cost_usd": snapshot.cost.cost_usd,
        },
        "messages": [_message_record_to_dict(m) for m in snapshot.messages],
        "tool_calls": [_tool_call_to_dict(t) for t in snapshot.tool_calls],
        "saved_at": snapshot.saved_at.isoformat(),
    }


def snapshot_from_dict(data: Mapping[str, object]) -> SessionSnapshot:
    schema_version = _require_int(data, "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {schema_version}")

    cost_raw = _require_mapping(data, "cost")
    messages_raw = _require_list(data, "messages")
    tool_calls_raw = _require_list(data, "tool_calls")
    saved_at_raw = _require_str(data, "saved_at")

    return SessionSnapshot(
        schema_version=schema_version,
        session_id=_require_str(data, "session_id"),
        model=_require_str(data, "model"),
        system_prompt=_optional_str(data.get("system_prompt"), label="system_prompt"),
        cost=SessionCostSnapshot(
            input_tokens=_require_int(cost_raw, "input_tokens"),
            output_tokens=_require_int(cost_raw, "output_tokens"),
            cache_read_tokens=_require_int(cost_raw, "cache_read_tokens"),
            cache_write_tokens=_require_int(cost_raw, "cache_write_tokens"),
            cost_usd=_require_float(cost_raw, "cost_usd"),
        ),
        messages=tuple(_message_record_from_dict(item) for item in messages_raw),
        tool_calls=tuple(_tool_call_from_dict(item) for item in tool_calls_raw),
        saved_at=datetime.fromisoformat(saved_at_raw),
        max_turns=_optional_int(data.get("max_turns")),
        working_dir=_optional_str(data.get("working_dir"), label="working_dir"),
        metadata=FrozenJsonObject.capture(_optional_mapping(data.get("metadata"))),
    )


class FileSessionStore:
    """Atomic JSON persistence for ``SessionSnapshot`` under a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, session_id: str) -> Path:
        safe_id = checked_session_id(session_id)
        return self._root / f"{safe_id}.json"

    def save(self, snapshot: SessionSnapshot) -> Path:
        path = self.path_for(snapshot.session_id)
        payload = snapshot_to_dict(snapshot)
        text = json.dumps(payload, indent=2) + "\n"
        atomic_write_text(path, text, mode=0o600)
        return path

    def load(self, session_id: str) -> SessionSnapshot:
        """Read a snapshot, or raise :class:`SessionResumeError`.

        Every failure is typed so a caller holding the handle can tell "never
        saved" from "written by an older dream" from "truncated file", and pick
        the matching recovery instead of parsing messages.
        """
        path = self.path_for(session_id)
        if not path.is_file():
            raise SessionResumeError(
                f"session snapshot not found: {path}",
                reason="missing",
                session_id=session_id,
            )
        try:
            raw = path.read_text(encoding="utf-8")
            parsed: object = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionResumeError(
                f"session snapshot unreadable: {path}",
                reason="corrupt",
                session_id=session_id,
                cause=exc,
            ) from exc
        if not isinstance(parsed, Mapping):
            raise SessionResumeError(
                f"expected JSON object in {path}",
                reason="corrupt",
                session_id=session_id,
            )
        version = parsed.get("schema_version")
        if version != SCHEMA_VERSION:
            raise SessionResumeError(
                f"unsupported schema_version {version!r} in {path} "
                f"(this dream reads {SCHEMA_VERSION})",
                reason="schema_mismatch",
                session_id=session_id,
            )
        try:
            return snapshot_from_dict(parsed)
        except (ValueError, TypeError, KeyError) as exc:
            raise SessionResumeError(
                f"session snapshot failed to decode: {path}",
                reason="corrupt",
                session_id=session_id,
                cause=exc,
            ) from exc

    def exists(self, session_id: str) -> bool:
        return self.path_for(session_id).is_file()

    def list_sessions(self) -> list[str]:
        """Session ids with a snapshot on disk, sorted. Empty when unwritten."""
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json") if p.is_file())

    def delete(self, session_id: str) -> bool:
        """Drop a snapshot. Returns whether a file was actually removed.

        The recovery half of the handle contract: after a failed resume the
        caller clears the spent snapshot so the next attempt starts clean.
        """
        path = self.path_for(session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True


# --- private JSON helpers ----------------------------------------------------


def _message_record_to_dict(record: ConversationMessageRecord) -> dict[str, object]:
    return {
        "role": record.role,
        "content": [_block_record_to_dict(b) for b in record.content],
    }


def _block_record_to_dict(block: ContentBlockRecord) -> dict[str, object]:
    if isinstance(block, TextBlockRecord):
        return {"kind": "text", "text": block.text}
    if isinstance(block, ImageBlockRecord):
        return {"kind": "image", "media_type": block.media_type, "data": block.data}
    if isinstance(block, ToolUseBlockRecord):
        return {
            "kind": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": json_dict_from_mapping(block.input.thaw()),
        }
    if isinstance(block, ToolResultBlockRecord):
        return {
            "kind": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise TypeError(f"unsupported block record: {type(block).__name__}")


def _tool_call_to_dict(record: ToolCallRecord) -> dict[str, object]:
    return {
        "tool_use_id": record.tool_use_id,
        "tool_name": record.tool_name,
        "input": json_dict_from_mapping(record.input.thaw()),
        "result_content": record.result_content,
        "is_error": record.is_error,
    }


def _message_record_from_dict(raw: object) -> ConversationMessageRecord:
    data = _as_mapping(raw, "message")
    role_raw = _require_str(data, "role")
    if role_raw == "user":
        role: RoleLiteral = "user"
    elif role_raw == "assistant":
        role = "assistant"
    else:
        raise ValueError(f"invalid message role: {role_raw!r}")
    content_raw = _require_list(data, "content")
    return ConversationMessageRecord(
        role=role,
        content=tuple(_block_record_from_dict(item) for item in content_raw),
    )


def _block_record_from_dict(raw: object) -> ContentBlockRecord:
    data = _as_mapping(raw, "content block")
    kind = _require_str(data, "kind")
    if kind == "text":
        return TextBlockRecord(kind="text", text=_require_str(data, "text"))
    if kind == "image":
        return ImageBlockRecord(
            kind="image",
            media_type=_require_str(data, "media_type"),
            data=_require_str(data, "data"),
        )
    if kind == "tool_use":
        input_raw = _require_mapping(data, "input")
        return ToolUseBlockRecord(
            kind="tool_use",
            id=_require_str(data, "id"),
            name=_require_str(data, "name"),
            input=FrozenJsonObject.capture(input_raw),
        )
    if kind == "tool_result":
        return ToolResultBlockRecord(
            kind="tool_result",
            tool_use_id=_require_str(data, "tool_use_id"),
            content=_require_str(data, "content"),
            is_error=_require_bool(data, "is_error"),
        )
    raise ValueError(f"unknown content block kind: {kind!r}")


def _tool_call_from_dict(raw: object) -> ToolCallRecord:
    data = _as_mapping(raw, "tool_call")
    input_raw = _require_mapping(data, "input")
    result_content = data.get("result_content")
    is_error = data.get("is_error")
    return ToolCallRecord(
        tool_use_id=_require_str(data, "tool_use_id"),
        tool_name=_require_str(data, "tool_name"),
        input=FrozenJsonObject.capture(input_raw),
        result_content=str(result_content) if result_content is not None else None,
        is_error=bool(is_error) if is_error is not None else None,
    )


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"expected {label} object, got {type(value).__name__}")
    return value


def _require_mapping(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"missing or invalid {key!r}")
    return value


def _require_list(data: Mapping[str, object], key: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"missing or invalid {key!r}")
    return value


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"missing or invalid {key!r}")
    return value


def _require_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"missing or invalid {key!r}")
    return value


def _require_float(data: Mapping[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"missing or invalid {key!r}")
    return float(value)


def _require_bool(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"missing or invalid {key!r}")
    return value


def _optional_str(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_turns must be an integer or null")
    return value


def _optional_mapping(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be an object")
    return value


def messages_from_records(
    records: Sequence[ConversationMessageRecord],
) -> list[ConversationMessage]:
    return [record_to_message(r) for r in records]


@dataclass(frozen=True)
class SessionCostFields:
    """Plain cost counters used to avoid a circular import with ``dream.session``."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


def cost_snapshot_from_fields(fields: SessionCostFields) -> SessionCostSnapshot:
    """Build a snapshot cost block from plain counters."""
    return SessionCostSnapshot(
        input_tokens=fields.input_tokens,
        output_tokens=fields.output_tokens,
        cache_read_tokens=fields.cache_read_tokens,
        cache_write_tokens=fields.cache_write_tokens,
        cost_usd=fields.cost_usd,
    )
