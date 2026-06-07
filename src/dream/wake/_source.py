"""Spec 06.5 slice 2 — typed wake source discriminator.

The wake source is *why* a heartbeat fired. Slice 1 carried it as a free
string placeholder. Slice 2 pins the shape so we can:

- record it structurally in the heartbeat jsonl (per spec example),
- splice a short ``label`` into the wake prompt stimulus, and
- route different sources through different policies later (Spec 07).

Variants live as separate frozen dataclasses with a literal ``kind`` tag
and a custom ``label`` for prompt insertion. ``WakeSource`` is the union
type alias used at API boundaries; it is a ``types.UnionType`` at
runtime so ``isinstance(src, WakeSource)`` works.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class CronWake:
    """A cron schedule fired. ``cron_kind`` identifies *which* schedule
    (e.g. ``"doc-garden"``); ``run_id`` is the scheduler's per-fire id
    if any (optional in slice 2; spec 07 adds the target/run engine)."""

    cron_kind: str
    run_id: str | None = None
    kind: Literal["cron"] = "cron"

    @property
    def label(self) -> str:
        return f"cron:{self.cron_kind}"


@dataclass(frozen=True)
class IdleTimerWake:
    """Background watchdog: the agent has been idle for N minutes."""

    idle_minutes: int
    kind: Literal["idle_timer"] = "idle_timer"

    @property
    def label(self) -> str:
        return f"idle_timer:{self.idle_minutes}m"


@dataclass(frozen=True)
class InboundMessageWake:
    """An external message arrived. ``channel`` is the transport
    identifier; ``message_ref`` is an opaque id the caller can use to
    correlate."""

    channel: str
    message_ref: str
    kind: Literal["inbound_message"] = "inbound_message"

    @property
    def label(self) -> str:
        return f"inbound_message:{self.channel}"


@dataclass(frozen=True)
class ManualWake:
    """The REPL ``/wake`` slash command or a CLI ``dream wake`` invocation."""

    kind: Literal["manual"] = "manual"

    @property
    def label(self) -> str:
        return "manual"


WakeSource = CronWake | IdleTimerWake | InboundMessageWake | ManualWake


def wake_source_to_dict(src: WakeSource) -> dict[str, Any]:
    """Serialise a wake source to a JSON-safe dict.

    ``None``-valued optional fields (currently only ``CronWake.run_id``)
    are omitted to keep the on-disk record tidy.
    """
    if isinstance(src, CronWake):
        out: dict[str, Any] = {"kind": "cron", "cron_kind": src.cron_kind}
        if src.run_id is not None:
            out["run_id"] = src.run_id
        return out
    if isinstance(src, IdleTimerWake):
        return {"kind": "idle_timer", "idle_minutes": src.idle_minutes}
    if isinstance(src, InboundMessageWake):
        return {
            "kind": "inbound_message",
            "channel": src.channel,
            "message_ref": src.message_ref,
        }
    if isinstance(src, ManualWake):
        return {"kind": "manual"}
    raise TypeError(f"unknown wake source type: {type(src)!r}")


def wake_source_from_dict(d: dict[str, Any]) -> WakeSource:
    """Inverse of :func:`wake_source_to_dict`. Raises on unknown/missing kind."""
    kind = d.get("kind")
    if kind is None:
        raise ValueError("wake source dict is missing required 'kind' field")
    if kind == "cron":
        return CronWake(cron_kind=d["cron_kind"], run_id=d.get("run_id"))
    if kind == "idle_timer":
        idle_minutes = d["idle_minutes"]
        # ``int(...)`` would silently accept bool/float and truncate. Require a
        # real ``int`` and reject ``bool`` (an ``int`` subclass) so corrupted
        # records raise instead of being coerced.
        if not isinstance(idle_minutes, int) or isinstance(idle_minutes, bool):
            raise ValueError(
                f"idle_minutes must be an int, got {type(idle_minutes).__name__}"
            )
        return IdleTimerWake(idle_minutes=idle_minutes)
    if kind == "inbound_message":
        return InboundMessageWake(channel=d["channel"], message_ref=d["message_ref"])
    if kind == "manual":
        return ManualWake()
    raise ValueError(f"unknown wake source kind: {kind!r}")
