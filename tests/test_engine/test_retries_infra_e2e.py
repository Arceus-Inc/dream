"""End-to-end component paths for retries infra (no live provider required).

Exercises classifier → pool → FailoverStreamer as one stack, mirroring what
``build_harness`` wires for a session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest

from dream.api.credentials import Credential, CredentialPool
from dream.api.error_classify import FailureKind, classify_failure
from dream.api.failover import NoLiveSubstrate
from dream.engine._cost import UsageSnapshot
from dream.engine._events import AssistantTurnComplete, StreamEvent
from dream.engine._failover_streamer import FailoverStreamer
from dream.engine._failover_wire import StreamerParts, slots_for_session
from dream.engine._substrate_slot import SubstrateSlot


def _complete() -> AssistantTurnComplete:
    return AssistantTurnComplete(blocks=[], usage=UsageSnapshot(input_tokens=1, output_tokens=1))


def _http(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return httpx.HTTPStatusError(
        f"{status}", request=req, response=httpx.Response(status, request=req)
    )


class _Script:
    def __init__(self, steps: Sequence[object]) -> None:
        self._steps = list(steps)
        self.calls = 0

    async def stream_turn(self, messages: Sequence[object]) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        assert isinstance(step, list)
        for item in step:
            assert isinstance(item, StreamEvent)
            yield item


async def _noop(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_e2e_stack_benches_first_key_then_succeeds_on_second() -> None:
    a = _Script([_http(503), _http(503), _http(503)])
    b = _Script([[_complete()]])
    pool = CredentialPool(
        "primary",
        (
            Credential(label="a", key="ka", substrate="primary"),
            Credential(label="b", key="kb", substrate="primary"),
        ),
    )
    streamer = FailoverStreamer(
        [SubstrateSlot(name="primary", pool=pool, streamers={"a": a, "b": b})],
        retries_per_credential=2,
        sleep=_noop,
    )

    events = [e async for e in streamer.stream_turn([])]

    assert classify_failure(_http(503)).kind is FailureKind.SERVER_TRANSIENT
    assert a.calls == 3
    assert b.calls == 1
    assert isinstance(events[0], AssistantTurnComplete)
    assert pool.get("a").is_benched()


@pytest.mark.asyncio
async def test_e2e_two_substrates_when_pool_empty() -> None:
    primary = _Script([_http(401)])
    backup = _Script([[_complete()]])
    streamer = FailoverStreamer.from_named_streamers(
        [("primary", primary), ("backup", backup)],
        sleep=_noop,
    )
    events = [e async for e in streamer.stream_turn([])]
    assert primary.calls == 1
    assert backup.calls == 1
    assert isinstance(events[0], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_e2e_exhaustion_is_typed() -> None:
    only = _Script([_http(429)] * 3)
    streamer = FailoverStreamer.from_named_streamers(
        [("primary", only)], retries_per_credential=2, sleep=_noop
    )
    with pytest.raises(NoLiveSubstrate):
        _ = [e async for e in streamer.stream_turn([])]


def test_e2e_credentials_toml_slot_graph(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    path = harness / "credentials.toml"
    path.write_text(
        '[[primary]]\nkey = "sk-1"\nlabel = "one"\n\n[[backup]]\nkey = "sk-2"\nlabel = "two"\n',
        encoding="utf-8",
    )
    path.chmod(0o600)
    parts = StreamerParts(
        model="m",
        base_url="https://example.test/v1",
        system_prompt="s",
        extra_params=None,
    )
    slots = slots_for_session(
        api_key="unused",
        parts=parts,
        credentials_path=path,
        active_substrate="primary",
    )
    assert [s.name for s in slots] == ["primary", "backup"]
    assert {c.label for c in slots[0].pool.all_credentials()} == {"one"}
    assert {c.label for c in slots[1].pool.all_credentials()} == {"two"}
