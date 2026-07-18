"""FailoverStreamer — beats survive 429s/5xx (bounded retry) and dead providers (rotation).

Harvested 2026-07-18: the engine's turn path had zero retry — one 429 out of
``raise_for_status()`` killed the beat. The wrapper sits at the TurnStreamer seam, so the
semantics hold for every substrate behind it:

- retry the SAME substrate on retryable failures (429/5xx/transport), bounded, with backoff
- rotate to the next substrate when retries exhaust (FailoverPolicy — spec 02 §12-16)
- turn boundaries only: an error after events were yielded re-raises (no transparent replay)
- transparent to the agent: no prompt-history injection; observability via on_event
- chain exhausted -> NoLiveSubstrate (criterion 17), never a silent retry-forever loop
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest

from dream.api.failover import NoLiveSubstrate
from dream.engine._cost import UsageSnapshot
from dream.engine._events import AssistantTurnComplete, StreamEvent
from dream.engine._failover_streamer import FailoverStreamer


def _turn_complete() -> AssistantTurnComplete:
    return AssistantTurnComplete(blocks=[], usage=UsageSnapshot(input_tokens=1, output_tokens=1))


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://x/v1/chat/completions")
    return httpx.HTTPStatusError(
        f"{status}", request=request, response=httpx.Response(status, request=request)
    )


class _ScriptedStreamer:
    """Yields per stream_turn call: an exception instance (raised) or a list of events."""

    def __init__(self, script: Sequence[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def stream_turn(self, messages: Sequence[Any]) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        for event in step:
            yield event
            if isinstance(event, BaseException):  # pragma: no cover - guard
                raise event
        # a mid-stream failure: [event, exception]
        return


class _MidStreamFails:
    """Yields one event, then raises — the mid-turn case."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def stream_turn(self, messages: Sequence[Any]) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        yield _turn_complete()
        raise self._exc


async def _no_sleep(_: float) -> None:
    return None


async def _collect(streamer: FailoverStreamer) -> list[StreamEvent]:
    return [event async for event in streamer.stream_turn([])]


async def test_retries_the_same_substrate_on_429_then_succeeds() -> None:
    primary = _ScriptedStreamer([_http_error(429), [_turn_complete()]])
    failover = FailoverStreamer([("azure", primary)], sleep=_no_sleep)

    events = await _collect(failover)

    assert primary.calls == 2
    assert isinstance(events[0], AssistantTurnComplete)


async def test_rotates_to_the_fallback_when_retries_exhaust() -> None:
    primary = _ScriptedStreamer([_http_error(503), _http_error(503), _http_error(503)])
    backup = _ScriptedStreamer([[_turn_complete()]])
    seen: list[dict[str, Any]] = []
    failover = FailoverStreamer(
        [("azure", primary), ("backup", backup)],
        retries_per_substrate=2,
        sleep=_no_sleep,
        on_event=seen.append,
    )

    events = await _collect(failover)

    assert primary.calls == 3  # first try + 2 retries
    assert backup.calls == 1
    assert isinstance(events[0], AssistantTurnComplete)
    assert any(event["type"] == "substrate.failover" for event in seen)
    # sticky: the NEXT turn goes straight to the backup (no auto switch-back — §16)
    backup._script.append([_turn_complete()])
    await _collect(failover)
    assert primary.calls == 3


async def test_auth_failure_rotates_without_burning_retries() -> None:
    primary = _ScriptedStreamer([_http_error(401)])
    backup = _ScriptedStreamer([[_turn_complete()]])
    failover = FailoverStreamer([("azure", primary), ("backup", backup)], sleep=_no_sleep)

    await _collect(failover)

    assert primary.calls == 1  # auth is substrate-level; retrying the same key is pointless
    assert backup.calls == 1


async def test_client_error_raises_immediately() -> None:
    primary = _ScriptedStreamer([_http_error(400)])
    backup = _ScriptedStreamer([[_turn_complete()]])
    failover = FailoverStreamer([("azure", primary), ("backup", backup)], sleep=_no_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(failover)
    assert backup.calls == 0  # our request is malformed; rotation cannot help


async def test_chain_exhaustion_raises_no_live_substrate() -> None:
    primary = _ScriptedStreamer([_http_error(429)] * 3)
    failover = FailoverStreamer([("azure", primary)], retries_per_substrate=2, sleep=_no_sleep)

    with pytest.raises(NoLiveSubstrate):
        await _collect(failover)


async def test_mid_turn_failure_reraises_instead_of_replaying() -> None:
    primary = _MidStreamFails(_http_error(429))
    backup = _ScriptedStreamer([[_turn_complete()]])
    failover = FailoverStreamer([("azure", primary), ("backup", backup)], sleep=_no_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(failover)
    assert primary.calls == 1
    assert backup.calls == 0  # replaying a half-yielded turn would duplicate events (§13)


async def test_transport_errors_are_retryable() -> None:
    primary = _ScriptedStreamer([httpx.ConnectError("boom"), [_turn_complete()]])
    failover = FailoverStreamer([("azure", primary)], sleep=_no_sleep)

    events = await _collect(failover)

    assert primary.calls == 2
    assert isinstance(events[0], AssistantTurnComplete)
