"""Transparent failover across substrates (Spec 02 §12-16).

The policy carries the dropdown order from ``substrates.toml`` and tracks
which substrate is currently active. It does **not** know about credential
pools — that's :mod:`dream.api.credentials`' job. The dispatcher calls
:meth:`FailoverPolicy.next_substrate` when *all* credentials for the active
substrate are benched; this module's only concern is which substrate comes
next and whether the switch is allowed mid-turn.

Three invariants the spec calls out by name (and that are easy to
accidentally break):

1. Failover is **transparent to the agent** — no event is injected into
   prompt history; the operator-facing event goes through ``on_event``.
2. Failover happens **at turn boundaries only** unless
   :attr:`allow_mid_turn` is explicitly set. The dispatcher is expected
   to consult :meth:`allow_mid_turn_switch` before issuing a switch
   inside an in-flight turn.
3. ``health.recovered`` is emitted **without** auto-switching back, so a
   flapping substrate doesn't cause the runner to flap with it. Switch-back
   is operator-driven.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class NoLiveSubstrate(RuntimeError):
    """No substrate in the failover chain has any live credentials.

    Spec criterion 17 — the dispatcher returns this to the turn FSM (Spec
    03) which decides whether to retry the turn or end the task. Crucially
    *not* a silent retry-forever loop.
    """


EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class FailoverPolicy:
    """Stateful policy object that walks the substrate dropdown.

    ``order`` is the ranked list of substrate names (most-preferred first),
    sourced from ``.harness/substrates.toml`` ``priority``. ``on_event`` is
    invoked for every failover, recovery, or degradation; it's the wire to
    the runner's observability layer.
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

    # --- queries -------------------------------------------------------------

    def active(self) -> str:
        """The substrate the dispatcher should route to right now."""
        return self._active

    def allow_mid_turn_switch(self) -> bool:
        """Whether the dispatcher is allowed to switch mid-turn.

        Default ``False`` per §13: mid-turn switching breaks tool-call
        correlation and replays cost. An explicit ``allow_mid_turn=True``
        opts in.
        """
        return self.allow_mid_turn

    # --- transitions ---------------------------------------------------------

    def next_substrate(self, *, after: str, reason: str = "pool_exhausted") -> str:
        """Advance one step in the failover chain.

        Updates :meth:`active` and emits ``substrate.failover``. Raises
        :class:`NoLiveSubstrate` when ``after`` is the last entry (criterion
        17) or isn't in the configured order at all (operator removed the
        active substrate mid-session — deferred to next start, but the
        in-memory chain is what we honour).
        """
        if after != self._active:
            # Advance only from the true current position; a stale caller value
            # could otherwise cause a no-op or backward switch and break chain
            # exhaustion.
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
            {
                "type": "substrate.failover",
                "from": after,
                "to": chosen,
                "reason": reason,
            }
        )
        return chosen

    def record_probe(self, substrate: str, *, healthy: bool) -> None:
        """Record a background health probe result (§16).

        Emits ``health.recovered`` on a clean → up transition and
        ``health.degraded`` on a fresh degradation, but **never** switches
        active. Switch-back is operator-driven so the runner doesn't flap.
        """
        previous = self._probe_state.get(substrate)
        self._probe_state[substrate] = healthy

        if healthy and previous is False:
            self._emit({"type": "substrate.health.recovered", "substrate": substrate})
        elif not healthy and previous is not False:
            # First-ever probe failing also counts as a fresh degradation.
            self._emit({"type": "substrate.health.degraded", "substrate": substrate})

    # --- internals -----------------------------------------------------------

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event)


__all__ = [
    "EventCallback",
    "FailoverPolicy",
    "NoLiveSubstrate",
]
