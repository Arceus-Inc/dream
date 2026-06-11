"""The async-open chokepoint — ``Harness._ensure_open`` (spec 15 wiring).

MCP connect + plugin load are async/IO, so they run once via an opener
stashed on the config. The opener must fire exactly once regardless of
how the harness is entered — ``async with``, a bare ``start_session``,
or repeated calls — and its teardown must run on ``aclose``.
"""

from __future__ import annotations

import pytest

from dream.harness import Harness, HarnessConfig


def _recording_opener() -> tuple[HarnessConfig, list[str]]:
    log: list[str] = []

    async def teardown() -> None:
        log.append("teardown")

    async def opener(harness: Harness) -> object:
        log.append("open")
        return teardown

    return HarnessConfig(_async_opener=opener), log  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_opener_runs_once_on_start_session(tmp_path: object) -> None:
    config, log = _recording_opener()
    harness = Harness(config)
    await harness.start_session()
    await harness.start_session()  # idempotent — opener must not re-run
    assert log == ["open"]


@pytest.mark.asyncio
async def test_bare_start_session_opens_without_async_with() -> None:
    # The example --once paths call start_session directly (no `async with`);
    # the chokepoint is start_session, not __aenter__, so wiring still fires.
    config, log = _recording_opener()
    harness = Harness(config)
    await harness.start_session()
    assert log == ["open"]


@pytest.mark.asyncio
async def test_async_with_opens_and_teardown_on_close() -> None:
    config, log = _recording_opener()
    async with Harness(config):
        pass
    assert log == ["open", "teardown"]


@pytest.mark.asyncio
async def test_teardown_runs_once() -> None:
    config, log = _recording_opener()
    harness = Harness(config)
    await harness.start_session()
    await harness.aclose()
    await harness.aclose()  # idempotent teardown
    assert log == ["open", "teardown"]


@pytest.mark.asyncio
async def test_no_opener_is_a_noop() -> None:
    harness = Harness(HarnessConfig())
    await harness.start_session()
    await harness.aclose()  # must not raise
