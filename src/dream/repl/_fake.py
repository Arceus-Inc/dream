"""Always-failing substrate for demoing failover without burning real keys.

Satisfies the :class:`dream.api.substrate.Substrate` Protocol structurally.
Every call raises an exception whose class name contains ``Authentication``
(matching the OpenAI SDK's :class:`openai.AuthenticationError`), so the
dispatcher's outcome classifier benches the credential as ``auth``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from dream.api.substrate import CompletionResult, HealthReport


class _FakeAuthenticationError(Exception):
    """Has ``Authentication`` in its name, so the classifier treats it as 401."""


class FakeFailingSubstrate:
    """Drop-in Substrate that always raises an auth-style error."""

    name: str

    def __init__(self, name: str = "fake-fail", max_window_tokens: int = 8_192) -> None:
        self.name = name
        self._max_window_tokens = max_window_tokens

    def complete(self, prompt: str, params: dict[str, Any] | None = None) -> CompletionResult:
        raise _FakeAuthenticationError(
            f"fake substrate {self.name!r} always fails (would 401 in production)"
        )

    def stream(self, prompt: str, params: dict[str, Any] | None = None) -> Iterator[str]:
        raise _FakeAuthenticationError(
            f"fake substrate {self.name!r} always fails (would 401 in production)"
        )
        yield ""  # pragma: no cover — unreachable, satisfies generator type

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    def max_window(self) -> int:
        return self._max_window_tokens

    def health(self) -> HealthReport:
        return HealthReport(state="down", detail="FakeFailingSubstrate", latency_ms=0.0)


__all__ = ["FakeFailingSubstrate"]
