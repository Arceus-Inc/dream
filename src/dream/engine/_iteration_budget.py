"""Per-session iteration budget — thread-safe consume/refund counter.

Hermes-style: each act-loop holds an :class:`IterationBudget` capped at
``max_turns``. Turns that only call programmatic tools (``execute_code``,
``spawn_subagent``) may refund so they do not burn the parent's turn cap the
same way; a separate refund ceiling keeps an uncooperative model from looping
forever. Nested subagent sessions keep their own independent budget.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

PROGRAMMATIC_TOOLS: frozenset[str] = frozenset({"execute_code", "spawn_subagent"})


class IterationBudget:
    """Thread-safe iteration counter for one agent session.

    ``consume`` returns ``False`` when the soft cap is hit. ``refund`` restores
    one slot for programmatic turns, but only up to ``max_refunds`` times so the
    act-loop always has a finite bound (at most ``max_total + max_refunds``
    iterations).
    """

    def __init__(self, max_total: int, *, max_refunds: int | None = None) -> None:
        if max_total < 0:
            raise ValueError(f"max_total must be >= 0; got {max_total}")
        # Default: as many refunds as soft turns → hard ceiling 2x max_total.
        refunds = max_total if max_refunds is None else max_refunds
        if refunds < 0:
            raise ValueError(f"max_refunds must be >= 0; got {refunds}")
        self._max_total = max_total
        self._max_refunds = refunds
        self._used = 0
        self._refunds = 0
        self._lock = threading.Lock()

    @property
    def max_total(self) -> int:
        return self._max_total

    @property
    def max_refunds(self) -> int:
        return self._max_refunds

    @property
    def refunds(self) -> int:
        with self._lock:
            return self._refunds

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self._max_total - self._used)

    def consume(self) -> bool:
        """Try to consume one iteration. Returns ``True`` if allowed."""
        with self._lock:
            if self._used >= self._max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> bool:
        """Give back one iteration if under the refund ceiling.

        Returns ``True`` when a slot was restored.
        """
        with self._lock:
            if self._used <= 0 or self._refunds >= self._max_refunds:
                return False
            self._used -= 1
            self._refunds += 1
            return True


def is_programmatic_only(tool_names: Iterable[str]) -> bool:
    """True when the set is non-empty and every name is in :data:`PROGRAMMATIC_TOOLS`."""
    names = frozenset(tool_names)
    return bool(names) and names <= PROGRAMMATIC_TOOLS


__all__ = [
    "PROGRAMMATIC_TOOLS",
    "IterationBudget",
    "is_programmatic_only",
]
