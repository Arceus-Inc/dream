"""Transparent failover across substrates (Spec 02 §12-16).

The policy carries the dropdown order and tracks which substrate is currently
active. It does **not** know about credential pools — that's
:mod:`dream.api.credentials`' job. The dispatcher calls
:meth:`FailoverPolicy.next_substrate` when *all* credentials for the active
substrate are benched; this module's only concern is which substrate comes
next and whether the switch is allowed mid-turn.

Three invariants the spec calls out by name:

1. Failover is **transparent to the agent** — no event is injected into
   prompt history; the operator-facing event goes through ``on_event``.
2. Failover happens **at turn boundaries only** unless
   :attr:`allow_mid_turn` is explicitly set.
3. ``health.recovered`` is emitted **without** auto-switching back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dream.api.failover_events import (
    EventCallback,
    FailoverReason,
    SubstrateFailoverEvent,
    SubstrateHealthDegradedEvent,
    SubstrateHealthRecoveredEvent,
)


class NoLiveSubstrate(RuntimeError):
    """No substrate in the failover chain has any live credentials.

    Spec criterion 17 — the dispatcher returns this to the turn FSM (Spec
    03) which decides whether to retry the turn or end the task. Crucially
    *not* a silent retry-forever loop.
    """


@dataclass
class FailoverPolicy:
    """Stateful policy object that walks the substrate dropdown.

    ``order`` is the ranked list of substrate names (most-preferred first).
    ``on_event`` is invoked for every failover or health probe transition.
    """

    order: list[str]
    on_event: EventCallback | None = None
    allow_mid_turn: bool = False

    _active: str = field(default="", init=False)
    _probe_state: dict[str, bool] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.order:
            raise ValueError("FailoverPolicy requires a non-empty order")
        self._active = self.order[0]

    def active(self) -> str:
        """The substrate the dispatcher should route to right now."""
        return self._active

    def allow_mid_turn_switch(self) -> bool:
        """Whether the dispatcher is allowed to switch mid-turn."""
        return self.allow_mid_turn

    def next_substrate(
        self,
        *,
        after: str,
        reason: FailoverReason = FailoverReason.POOL_EXHAUSTED,
    ) -> str:
        """Advance one step in the failover chain.

        Updates :meth:`active` and emits :class:`SubstrateFailoverEvent`.
        Raises :class:`NoLiveSubstrate` when the chain is exhausted.
        """
        if after != self._active:
            raise ValueError(
                f"next_substrate(after={after!r}) does not match the active "
                f"substrate {self._active!r}; advance from the current active only"
            )
        try:
            idx = self.order.index(after)
        except ValueError as exc:
            raise NoLiveSubstrate(
                f"substrate {after!r} is not in the failover order {self.order!r}"
            ) from exc

        if idx + 1 >= len(self.order):
            raise NoLiveSubstrate(
                f"no fallback substrate after {after!r}; failover chain exhausted"
            )

        chosen = self.order[idx + 1]
        self._active = chosen
        self._emit(
            SubstrateFailoverEvent(
                from_substrate=after,
                to_substrate=chosen,
                reason=reason,
            )
        )
        return chosen

    def force_active(self, substrate: str) -> None:
        """Operator-driven switch-back (§16): set the active substrate directly."""
        if substrate not in self.order:
            raise ValueError(f"unknown substrate {substrate!r}; known: {self.order}")
        self._active = substrate

    def record_probe(self, substrate: str, *, healthy: bool) -> None:
        """Record a background health probe result (§16)."""
        previous = self._probe_state.get(substrate)
        self._probe_state[substrate] = healthy

        if healthy and previous is False:
            self._emit(SubstrateHealthRecoveredEvent(substrate=substrate))
        elif not healthy and previous is not False:
            self._emit(SubstrateHealthDegradedEvent(substrate=substrate))

    def _emit(
        self,
        event: SubstrateFailoverEvent
        | SubstrateHealthRecoveredEvent
        | SubstrateHealthDegradedEvent,
    ) -> None:
        if self.on_event is not None:
            self.on_event(event)


__all__ = [
    "EventCallback",
    "FailoverPolicy",
    "FailoverReason",
    "NoLiveSubstrate",
]
