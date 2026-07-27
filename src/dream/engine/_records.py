"""``TurnRecord`` + ``SessionEnd`` + jsonl serialization (Spec 03 stage 3a).

One ``TurnRecord`` is written per completed turn (including timeouts);
one ``SessionEnd`` is written on every session exit path. Both serialize
to single-line JSON with a ``kind`` discriminator so a single jsonl reader
can decode the audit trail.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.engine._outcomes import SessionOutcome, TurnOutcome, VerificationResult
from dream.utils.fs import compact_json


@dataclass(frozen=True)
class TurnRecord:
    turn_number: int
    started_at: datetime
    ended_at: datetime
    tools_called: tuple[str, ...]  # immutable: a frozen record's audit list must not mutate
    verification_result: VerificationResult
    outcome: TurnOutcome
    usage: UsageSnapshot
    notes: str = ""


@dataclass(frozen=True)
class SessionEnd:
    session_id: str
    started_at: datetime
    ended_at: datetime
    turns: int
    total_usage: UsageSnapshot
    outcome: SessionOutcome
    reason: str | None = None


def _encode(rec: TurnRecord | SessionEnd) -> dict[str, Any]:
    d = asdict(rec)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def to_jsonl_line(rec: TurnRecord | SessionEnd) -> str:
    payload = _encode(rec)
    if isinstance(rec, TurnRecord):
        payload = {"kind": "turn", **payload}
    elif isinstance(rec, SessionEnd):
        payload = {"kind": "session_end", **payload}
    else:  # pragma: no cover — exhaustive over the union
        raise TypeError(f"unknown record type: {type(rec).__name__}")
    return compact_json(payload, default=None)


def _decode_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def from_jsonl_line(line: str) -> TurnRecord | SessionEnd:
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError(f"jsonl record must be a JSON object, got {type(data).__name__}")
    kind = data.pop("kind", None)
    if kind == "turn":
        tools = data.get("tools_called")
        if not isinstance(tools, list):
            raise ValueError(f"tools_called must be a list, got {type(tools).__name__}")
        try:
            return TurnRecord(
                turn_number=data["turn_number"],
                started_at=_decode_datetime(data["started_at"]),
                ended_at=_decode_datetime(data["ended_at"]),
                tools_called=tuple(tools),
                verification_result=VerificationResult(data["verification_result"]),
                outcome=TurnOutcome(data["outcome"]),
                usage=UsageSnapshot(**data["usage"]),
                notes=data.get("notes", ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed turn record: {exc}") from exc
    if kind == "session_end":
        try:
            return SessionEnd(
                session_id=data["session_id"],
                started_at=_decode_datetime(data["started_at"]),
                ended_at=_decode_datetime(data["ended_at"]),
                turns=data["turns"],
                total_usage=UsageSnapshot(**data["total_usage"]),
                outcome=SessionOutcome(data["outcome"]),
                reason=data.get("reason"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed session_end record: {exc}") from exc
    raise ValueError(f"unknown jsonl record kind: {kind!r}")


__all__ = [
    "SessionEnd",
    "SessionOutcome",
    "TurnOutcome",
    "TurnRecord",
    "VerificationResult",
    "from_jsonl_line",
    "to_jsonl_line",
]
