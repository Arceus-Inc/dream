"""``TransitionEvent`` + ``TransitionBus`` (Spec 03 stage 3a).

The bus is an internal observer surface for session/turn FSM transitions.
Handlers are best-effort observers — they cannot veto a transition
(spec 03 #16). Exceptions in a listener are trapped, counted in
``failures``, and the most recent one is exposed via ``last_exception``
so a caller can inspect what went wrong without the engine taking a
side-effecting logging dependency (Spec 00 design rule 4).

This is distinct from ``dream.contracts.hook`` — that's the substrate-level
hook taxonomy (pre/post-tool, pre/post-compact, etc.) owned by spec 13.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TransitionEvent:
    kind: Literal["session", "turn"]
    from_state: str
    to_state: str

    @property
    def name(self) -> str:
        return f"{self.kind}.{self.from_state}.to.{self.to_state}"


TransitionListener = Callable[[TransitionEvent], None]


@dataclass
class TransitionBus:
    _listeners: list[TransitionListener] = field(default_factory=list)
    _failures: int = 0
    _last_exception: BaseException | None = None

    def register(self, listener: TransitionListener) -> None:
        self._listeners.append(listener)

    def fire(self, event: TransitionEvent) -> None:
        # Snapshot: a listener that registers/unregisters during dispatch must not
        # change this fire's invocation set (or cause unbounded re-entry).
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception as exc:
                self._failures += 1
                self._last_exception = exc

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def last_exception(self) -> BaseException | None:
        return self._last_exception


__all__ = ["TransitionBus", "TransitionEvent", "TransitionListener"]
