"""Hard per-session limits (Spec 13D).

A :class:`SessionLimiter` carries the running token / tool-call / network-call
counts for one session and reports the first cap it has reached via
:meth:`breached`. A fresh limiter is constructed per session, so counters never
roll forward (AC #20). The engine increments tokens (per turn usage) and
tool-calls (per dispatch) and aborts the session with
``SessionEnd(reason="limit-exceeded:{counter}")`` on breach.

The network-call counter's API exists, but it is auto-incremented only once an
egress chokepoint exists (net-allowlist enforcement) — see leftover spec #03.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionLimits:
    """The configurable per-session caps (spec defaults)."""

    max_llm_tokens: int = 5_000_000
    max_tool_calls: int = 2_000
    max_network_calls: int = 500


class SessionLimiter:
    """Mutable per-session counters with a first-breached check."""

    def __init__(self, limits: SessionLimits | None = None) -> None:
        self._limits = limits if limits is not None else SessionLimits()
        self._tokens = 0
        self._tool_calls = 0
        self._network_calls = 0

    def record_tokens(self, count: int) -> None:
        if count < 0:
            raise ValueError(f"token count must be >= 0, got {count}")
        self._tokens += count

    def record_tool_call(self) -> None:
        self._tool_calls += 1

    def record_network_call(self) -> None:
        self._network_calls += 1

    def breached(self) -> str | None:
        """Return the first reached cap's ``limit-exceeded:{counter}`` label, or None."""
        if self._tokens >= self._limits.max_llm_tokens:
            return "limit-exceeded:llm_tokens"
        if self._tool_calls >= self._limits.max_tool_calls:
            return "limit-exceeded:tool_calls"
        if self._network_calls >= self._limits.max_network_calls:
            return "limit-exceeded:network_calls"
        return None


__all__ = ["SessionLimiter", "SessionLimits"]
