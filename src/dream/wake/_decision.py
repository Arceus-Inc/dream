"""``HeartbeatDecision`` — the typed record one wake-cycle turn produces.

Serialized as a single jsonl line with ``kind: "heartbeat-decision"`` so
the same audit-trail reader that already handles ``kind: "turn"`` /
``kind: "session_end"`` (see ``dream.engine._records``) extends naturally
to wake records.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

HeartbeatAction = Literal["skip", "run"]
HeartbeatOutcome = Literal["decided", "missing"]


@dataclass(frozen=True)
class HeartbeatDecision:
    """One wake-cycle decision.

    ``outcome == "missing"`` means the model produced no valid ``heartbeat``
    tool call (no call at all, wrong tool name, or schema-invalid args). Slice
    2's skip-streak counter will NOT advance on a missing outcome — a missing
    is its own thing, not an honest skip. ``forced`` is the slice 2
    anti-coma flag; in slice 1 it is always ``False`` but is persisted so
    that pre-slice-2 records remain forward-compatible.
    """

    decided_at: datetime
    action: HeartbeatAction
    tasks: tuple[str, ...]
    reason: str
    wake_source: str | None = None
    forced: bool = False
    outcome: HeartbeatOutcome = "decided"


def _encode(rec: HeartbeatDecision) -> dict[str, Any]:
    d = asdict(rec)
    # datetimes -> iso string; tuple -> list (json has no tuples).
    d["decided_at"] = rec.decided_at.isoformat()
    d["tasks"] = list(rec.tasks)
    return d


def to_jsonl_line(rec: HeartbeatDecision) -> str:
    """Serialize one decision to a single jsonl line, with ``kind`` prefix."""
    payload = {"kind": "heartbeat-decision", **_encode(rec)}
    return json.dumps(payload, separators=(",", ":"))


def from_jsonl_line(line: str) -> HeartbeatDecision:
    """Decode a single jsonl line into a ``HeartbeatDecision``.

    Mirrors ``dream.engine._records.from_jsonl_line``: every shape error
    becomes a ``ValueError`` so the reader can skip a torn line instead of
    catching ``KeyError``/``TypeError``.
    """
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError(
            f"jsonl record must be a JSON object, got {type(data).__name__}"
        )
    kind = data.pop("kind", None)
    if kind != "heartbeat-decision":
        raise ValueError(f"expected kind='heartbeat-decision', got kind={kind!r}")
    try:
        tasks_raw = data["tasks"]
        if not isinstance(tasks_raw, list):
            raise ValueError(
                f"tasks must be a list, got {type(tasks_raw).__name__}"
            )
        return HeartbeatDecision(
            decided_at=datetime.fromisoformat(data["decided_at"]),
            action=data["action"],
            tasks=tuple(tasks_raw),
            reason=data["reason"],
            wake_source=data.get("wake_source"),
            forced=data.get("forced", False),
            outcome=data.get("outcome", "decided"),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed heartbeat-decision record: {exc}") from exc


__all__ = [
    "HeartbeatAction",
    "HeartbeatDecision",
    "HeartbeatOutcome",
    "from_jsonl_line",
    "to_jsonl_line",
]
