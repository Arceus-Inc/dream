"""``UsageSnapshot`` + ``CostTracker`` (Spec 03 stage 2).

The act-loop emits an ``AssistantTurnComplete`` carrying a ``UsageSnapshot``
per model turn. ``CostTracker`` is the minimal accumulator the session uses
to record per-turn usage and a running total — Spec 03 acceptance #8.

Pricing, budget enforcement, and forecasting consume this stream in a later
layer; this module only sums.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageSnapshot:
    """Token counters for a single model turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: UsageSnapshot) -> UsageSnapshot:
        return UsageSnapshot(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_tokens(self) -> int:
        """The billable wire total: input + output. Cache tokens are reported separately."""
        return self.input_tokens + self.output_tokens


class CostTracker:
    """Accumulates per-turn ``UsageSnapshot``s and exposes per-turn / total views."""

    def __init__(self) -> None:
        self._turns: list[UsageSnapshot] = []

    def add(self, usage: UsageSnapshot) -> None:
        self._turns.append(usage)

    @property
    def turns(self) -> int:
        return len(self._turns)

    @property
    def total(self) -> UsageSnapshot:
        total = UsageSnapshot()
        for u in self._turns:
            total = total + u
        return total

    @property
    def per_turn(self) -> list[UsageSnapshot]:
        """Return a *copy* — callers can't mutate the tracker through this view."""
        return list(self._turns)


__all__ = ["CostTracker", "UsageSnapshot"]
