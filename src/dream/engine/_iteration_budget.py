"""Per-session iteration budget — thread-safe consume/refund counter.

Hermes-style: each act-loop holds an :class:`IterationBudget` capped at
``max_turns``. Turns that only call programmatic tools (``execute_code``,
``spawn_subagent``) refund so they do not burn the parent's turn cap; nested
subagent sessions keep their own independent budget.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

PROGRAMMATIC_TOOLS: frozenset[str] = frozenset({"execute_code", "spawn_subagent"})


class IterationBudget:
    """Thread-safe iteration counter for one agent session.

    ``consume`` returns ``False`` when the cap is hit. ``refund`` restores one
    slot (never below zero) for programmatic / delegated turns.
    """

    def __init__(self, max_total: int) -> None:
        if max_total < 0:
            raise ValueError(f"max_total must be >= 0; got {max_total}")
        self._max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    @property
    def max_total(self) -> int:
        return self._max_total

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

    def refund(self) -> None:
        """Give back one iteration (e.g. for ``execute_code``-only turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1


def is_programmatic_only(tool_names: Iterable[str]) -> bool:
    """True when the set is non-empty and every name is in :data:`PROGRAMMATIC_TOOLS`."""
    names = frozenset(tool_names)
    return bool(names) and names <= PROGRAMMATIC_TOOLS


__all__ = [
    "PROGRAMMATIC_TOOLS",
    "IterationBudget",
    "is_programmatic_only",
]
