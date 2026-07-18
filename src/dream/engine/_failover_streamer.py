"""``FailoverStreamer`` — retry + substrate rotation at the TurnStreamer seam.

Harvested from the Spec-02 api layer (2026-07-18): the engine's turn path had zero
retry — ``raise_for_status()`` propagated one 429 straight out and killed the beat.
This wrapper composes :class:`~dream.api.failover.FailoverPolicy` (spec 02 §12-16)
with the engine's ``TurnStreamer`` seam so every substrate behind it inherits:

- bounded same-substrate retries with backoff on retryable failures (429/5xx/transport)
- rotation to the next substrate when retries exhaust; sticky (no auto switch-back, §16)
- turn boundaries only (§13): an error after events were yielded re-raises — replaying a
  half-yielded turn would duplicate events downstream
- transparency (§12): nothing is injected into prompt history; observability via ``on_event``
- :class:`~dream.api.failover.NoLiveSubstrate` on chain exhaustion (criterion 17)

With a single substrate configured (today's live stack: one Azure deployment) the wrapper
degrades to bounded retry — the rotation seam is ready for the day a second provider key
lands in the environment.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from dream.api.failover import EventCallback, FailoverPolicy, NoLiveSubstrate

if TYPE_CHECKING:
    from dream.engine._events import StreamEvent
    from dream.engine._messages import ConversationMessage

# 408/429 + server-side failures are worth retrying on the same substrate; auth
# (401/403) means the substrate's credential is dead — rotate without burning retries.
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_AUTH_STATUSES = frozenset({401, 403})


class FailoverStreamer:
    """Wrap an ordered chain of named TurnStreamers with retry + failover."""

    def __init__(
        self,
        streamers: Sequence[tuple[str, Any]],
        *,
        retries_per_substrate: int = 2,
        backoff_seconds: Sequence[float] = (1.0, 4.0),
        on_event: EventCallback | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not streamers:
            raise ValueError("FailoverStreamer requires at least one substrate")
        self._policy = FailoverPolicy(order=[name for name, _ in streamers], on_event=on_event)
        self._by_name = dict(streamers)
        self._retries = retries_per_substrate
        self._backoff = tuple(backoff_seconds)
        self._sleep = sleep

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        last_error: BaseException | None = None
        while True:
            name = self._policy.active()
            attempts = self._retries + 1
            for attempt in range(attempts):
                if attempt:
                    # Backoff before each retry; the last configured delay repeats.
                    delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                    await self._sleep(delay)
                yielded = False
                try:
                    async for event in self._by_name[name].stream_turn(messages):
                        yielded = True
                        yield event
                    return
                except httpx.HTTPStatusError as exc:
                    if yielded:  # mid-turn (§13): no transparent replay, ever
                        raise
                    last_error = exc
                    status = exc.response.status_code
                    if status in _AUTH_STATUSES:
                        break  # substrate-level failure — rotate, don't re-send a dead key
                    if status not in _RETRYABLE_STATUSES:
                        raise  # our request is malformed; neither retry nor rotation helps
                except httpx.TransportError as exc:
                    if yielded:
                        raise
                    last_error = exc
            try:
                self._policy.next_substrate(after=name)
            except NoLiveSubstrate:
                raise NoLiveSubstrate(
                    f"failover chain exhausted after {name!r}; last error: {last_error!r}"
                ) from last_error


__all__ = ["FailoverStreamer"]
