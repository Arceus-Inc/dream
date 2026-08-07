"""FailoverStreamer — beats survive 429s/5xx (bounded retry) and dead providers (rotation).

Pool-aware: exhausted credentials bench via CredentialPool; substrate rotation
only when the active pool has no live keys left.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import httpx
import pytest

from dream.api.credentials import AttemptOutcome, Credential, CredentialPool
from dream.api.failover import NoLiveSubstrate
from dream.api.failover_events import SubstrateFailoverEvent
from dream.engine._cost import UsageSnapshot
from dream.engine._events import AssistantTurnComplete, StreamEvent
from dream.engine._failover_streamer import FailoverStreamer
from dream.engine._loop import TurnStreamer
from dream.engine._substrate_slot import SubstrateSlot


def _turn_complete() -> AssistantTurnComplete:
    return AssistantTurnComplete(blocks=[], usage=UsageSnapshot(input_tokens=1, output_tokens=1))


def _http_error(status: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://x/v1/chat/completions")
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    return httpx.HTTPStatusError(
        f"{status}",
        request=request,
        response=httpx.Response(status, request=request, headers=headers),
    )


class _ScriptedStreamer:
    """Yields per stream_turn call: an exception instance (raised) or a list of events."""

    def __init__(self, script: Sequence[object]) -> None:
        self._script = list(script)
        self.calls = 0

    async def stream_turn(self, messages: Sequence[object]) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        assert isinstance(step, list)
        for event in step:
            yield event  # type: ignore[misc]


class _MidStreamFails:
    """Yields one event, then raises — the mid-turn case."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def stream_turn(self, messages: Sequence[object]) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        yield _turn_complete()
        raise self._exc


async def _no_sleep(_: float) -> None:
    return None


async def _collect(streamer: FailoverStreamer) -> list[StreamEvent]:
    return [event async for event in streamer.stream_turn([])]


def _solo(name: str, streamer: TurnStreamer) -> SubstrateSlot:
    pool = CredentialPool(name, (Credential(label="sole", key="unused", substrate=name),))
    return SubstrateSlot(name=name, pool=pool, streamers={"sole": streamer})


async def test_retries_the_same_substrate_on_429_then_succeeds() -> None:
    primary = _ScriptedStreamer([_http_error(429), [_turn_complete()]])
    failover = FailoverStreamer.from_named_streamers([("azure", primary)], sleep=_no_sleep)

    events = await _collect(failover)

    assert primary.calls == 2
    assert isinstance(events[0], AssistantTurnComplete)


async def test_rotates_to_the_fallback_when_retries_exhaust() -> None:
    primary = _ScriptedStreamer([_http_error(503), _http_error(503), _http_error(503)])
    backup = _ScriptedStreamer([[_turn_complete()]])
    seen: list[SubstrateFailoverEvent] = []

    def _on_event(event: object) -> None:
        if isinstance(event, SubstrateFailoverEvent):
            seen.append(event)

    failover = FailoverStreamer.from_named_streamers(
        [("azure", primary), ("backup", backup)],
        retries_per_credential=2,
        sleep=_no_sleep,
        on_event=_on_event,
    )

    events = await _collect(failover)

    assert primary.calls == 3  # first try + 2 retries
    assert backup.calls == 1
    assert isinstance(events[0], AssistantTurnComplete)
    assert seen
    assert seen[0].from_substrate == "azure"
    assert seen[0].to_substrate == "backup"
    # sticky: the NEXT turn goes straight to the backup (no auto switch-back — §16)
    backup._script.append([_turn_complete()])
    await _collect(failover)
    assert primary.calls == 3


async def test_auth_failure_rotates_without_burning_retries() -> None:
    primary = _ScriptedStreamer([_http_error(401)])
    backup = _ScriptedStreamer([[_turn_complete()]])
    failover = FailoverStreamer.from_named_streamers(
        [("azure", primary), ("backup", backup)], sleep=_no_sleep
    )

    await _collect(failover)

    assert primary.calls == 1  # auth is substrate-level; retrying the same key is pointless
    assert backup.calls == 1


async def test_client_error_raises_immediately() -> None:
    primary = _ScriptedStreamer([_http_error(400)])
    backup = _ScriptedStreamer([[_turn_complete()]])
    failover = FailoverStreamer.from_named_streamers(
        [("azure", primary), ("backup", backup)], sleep=_no_sleep
    )

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(failover)
    assert backup.calls == 0  # our request is malformed; rotation cannot help


async def test_chain_exhaustion_raises_no_live_substrate() -> None:
    primary = _ScriptedStreamer([_http_error(429)] * 3)
    failover = FailoverStreamer.from_named_streamers(
        [("azure", primary)], retries_per_credential=2, sleep=_no_sleep
    )

    with pytest.raises(NoLiveSubstrate):
        await _collect(failover)


async def test_mid_turn_failure_reraises_instead_of_replaying() -> None:
    primary = _MidStreamFails(_http_error(429))
    backup = _ScriptedStreamer([[_turn_complete()]])
    failover = FailoverStreamer.from_named_streamers(
        [("azure", primary), ("backup", backup)], sleep=_no_sleep
    )

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(failover)
    assert primary.calls == 1
    assert backup.calls == 0  # replaying a half-yielded turn would duplicate events (§13)


async def test_transport_errors_are_retryable() -> None:
    primary = _ScriptedStreamer([httpx.ConnectError("boom"), [_turn_complete()]])
    failover = FailoverStreamer.from_named_streamers([("azure", primary)], sleep=_no_sleep)

    events = await _collect(failover)

    assert primary.calls == 2
    assert isinstance(events[0], AssistantTurnComplete)


async def test_pool_rotates_keys_inside_one_substrate_before_failover() -> None:
    """Outer loop: bench key-a after transient exhaust, then key-b on same substrate."""
    key_a = _ScriptedStreamer([_http_error(429), _http_error(429), _http_error(429)])
    key_b = _ScriptedStreamer([[_turn_complete()]])
    pool = CredentialPool(
        "azure",
        (
            Credential(label="a", key="ka", substrate="azure"),
            Credential(label="b", key="kb", substrate="azure"),
        ),
    )
    slot = SubstrateSlot(name="azure", pool=pool, streamers={"a": key_a, "b": key_b})
    failover = FailoverStreamer([slot], retries_per_credential=2, sleep=_no_sleep)

    events = await _collect(failover)

    assert key_a.calls == 3
    assert key_b.calls == 1
    assert isinstance(events[0], AssistantTurnComplete)
    assert pool.get("a").is_benched()
    assert not pool.get("b").is_benched()


async def test_retry_after_header_is_capped_not_hours() -> None:
    sleeps: list[float] = []

    async def _capture(delay: float) -> None:
        sleeps.append(delay)

    primary = _ScriptedStreamer([_http_error(429, retry_after="7200"), [_turn_complete()]])
    failover = FailoverStreamer.from_named_streamers(
        [("azure", primary)], sleep=_capture, backoff_seconds=(0.0,)
    )

    await _collect(failover)

    assert sleeps
    assert max(sleeps) <= 60.0


async def test_record_attempt_success_resets_rung() -> None:
    pool = CredentialPool("azure", (Credential(label="sole", key="k", substrate="azure"),))
    pool.record_attempt("sole", outcome=AttemptOutcome.TRANSIENT_EXHAUSTED)
    assert pool.get("sole").is_benched()
    pool.record_attempt("sole", outcome=AttemptOutcome.SUCCESS)
    assert not pool.get("sole").is_benched()
