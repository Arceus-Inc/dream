"""``request_capability`` — the only escalation path out of a role.

A role cannot widen its own toolset mid-session (spec 10 decision #8).
When a bounded subagent decides it needs a capability it does not have, it
emits a :class:`RequestCapabilityEvent`. The event is recordable only; the
parent runner may choose to re-spawn the subagent with a wider manifest,
but the live manifest is *never* mutated in place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class RequestCapabilityEvent:
    """One recordable request for a capability the role lacks.

    Carries the role name, the desired tool name, a human-readable reason,
    and a UTC iso8601 timestamp. ``to_dict`` produces the on-stream payload
    shape used by the observability bus.
    """

    role: str
    tool_name: str
    reason: str
    ts: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = "role.request_capability"
        return payload


def request_capability(*, role: str, tool_name: str, reason: str) -> RequestCapabilityEvent:
    """Build a :class:`RequestCapabilityEvent` with a current timestamp."""
    return RequestCapabilityEvent(role=role, tool_name=tool_name, reason=reason)
