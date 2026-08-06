"""Unit tests for the pure browser_run package helpers (parse / observe / spawn)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from dream.tools.browser_run._observation import RecoveryAdvice, recovery_advice_for, summary_for
from dream.tools.browser_run._parse import looks_like_setup_error, parse_structured
from dream.tools.browser_run._spawn import (
    SpawnConfig,
    build_spawn_config,
    is_disabled,
    resolve_binary,
    resolve_cdp,
)
from dream.tools.browser_run._types import (
    BROWSER_RUN_BIN_KEY,
    BROWSER_RUN_CDP_URL_KEY,
    BROWSER_RUN_CDP_WS_KEY,
    BROWSER_RUN_DISABLED_KEY,
    BrowserKind,
    BrowserRunOutcome,
    BrowserRunStatus,
)


def test_recovery_advice_covers_every_status() -> None:
    for status in BrowserRunStatus:
        advice = recovery_advice_for(status)
        assert isinstance(advice, RecoveryAdvice)
        assert advice.root_cause and advice.safe_retry and advice.stop_condition


def test_success_gets_the_success_advice() -> None:
    assert recovery_advice_for(BrowserRunStatus.SUCCESS).root_cause == "browser_run completed"


def test_summary_for_success_and_failure() -> None:
    ok = summary_for(
        BrowserRunOutcome(
            status=BrowserRunStatus.SUCCESS, url="https://x.com", browser_kind=BrowserKind.CDP
        )
    )
    assert ok == "browser_run ok · cdp · https://x.com"
    no_url = summary_for(BrowserRunOutcome(status=BrowserRunStatus.SUCCESS))
    assert no_url == "browser_run ok · cdp"
    failed = summary_for(BrowserRunOutcome(status=BrowserRunStatus.TIMEOUT))
    assert failed == "browser_run timeout · cdp"


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ('print("hi")', {}),
        (
            '__BH_JSON__ {"page": {"url": "https://a.com", "title": "A"}}',
            {
                "page": {"url": "https://a.com", "title": "A"},
            },
        ),
        ('{"page": {"url": "https://b.com"}}', {"page": {"url": "https://b.com"}}),
        # bare page_info() shape is wrapped as {"page": ...}
        (
            '{"url": "https://c.com", "title": "C"}',
            {"page": {"url": "https://c.com", "title": "C"}},
        ),
        ("here is https://d.com/path", {"url": "https://d.com/path"}),
        ('{"dialog": {"text": "hi"}}', {"dialog": {"text": "hi"}}),
    ],
)
def test_parse_structured(stdout: str, expected: dict[str, object]) -> None:
    assert parse_structured(stdout) == expected


def test_looks_like_setup_error() -> None:
    assert looks_like_setup_error("chrome-not-running: DevToolsActivePort not found")
    assert looks_like_setup_error("browser-harness: remote debugging turned off")
    assert not looks_like_setup_error("regular output")


def test_resolve_cdp_ws_wins_then_url_then_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DREAM_CHROMIUM_CDP_URL", raising=False)
    monkeypatch.delenv("DREAM_CHROMIUM_CDP_WS", raising=False)
    assert resolve_cdp({}) == (None, None)
    assert resolve_cdp({BROWSER_RUN_CDP_URL_KEY: "http://x:9222"}) == ("http://x:9222", None)
    assert resolve_cdp({BROWSER_RUN_CDP_WS_KEY: "ws://x"}) == (None, "ws://x")
    # ws wins over url when both sit side by side.
    assert resolve_cdp(
        {BROWSER_RUN_CDP_WS_KEY: "ws://x", BROWSER_RUN_CDP_URL_KEY: "http://y:9222"}
    ) == (None, "ws://x")


def test_resolve_cdp_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DREAM_CHROMIUM_CDP_URL", raising=False)
    monkeypatch.setenv("DREAM_CHROMIUM_CDP_WS", "ws://env")
    assert resolve_cdp({}) == (None, "ws://env")


def test_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHORUS_DISABLE_BROWSER_RUN", raising=False)
    assert is_disabled({}) is False
    assert is_disabled({BROWSER_RUN_DISABLED_KEY: True}) is True
    assert is_disabled({BROWSER_RUN_DISABLED_KEY: "true"}) is True
    # the session flag is authoritative even when it says "off".
    monkeypatch.setenv("CHORUS_DISABLE_BROWSER_RUN", "1")
    assert is_disabled({BROWSER_RUN_DISABLED_KEY: False}) is False
    assert is_disabled({}) is True


def test_resolve_binary_env_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    harness = tmp_path / "browser-harness"
    harness.write_text("#!/usr/bin/env python3\nprint('x')\n", encoding="utf-8")
    harness.chmod(harness.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("DREAM_BROWSER_HARNESS_BIN", str(harness))
    assert resolve_binary({}) == str(harness)


def test_resolve_binary_session_key_beats_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_bin = tmp_path / "session-harness"
    session_bin.write_text("#!/usr/bin/env python3\nprint('s')\n", encoding="utf-8")
    session_bin.chmod(session_bin.stat().st_mode | stat.S_IXUSR)
    monkeypatch.delenv("DREAM_BROWSER_HARNESS_BIN", raising=False)
    # A non-executable env path must not shadow a valid session path.
    assert resolve_binary({BROWSER_RUN_BIN_KEY: str(session_bin)}) == str(session_bin)


def test_build_spawn_config_strips_cloud_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harness = tmp_path / "browser-harness"
    harness.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    harness.chmod(harness.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("BROWSER_USE_API_KEY", "secret")
    monkeypatch.setenv("BU_AUTOSPAWN", "true")
    config = build_spawn_config(
        metadata={
            BROWSER_RUN_BIN_KEY: str(harness),
            BROWSER_RUN_CDP_URL_KEY: "http://127.0.0.1:9222",
        },
        bu_name="ns1",
    )
    assert isinstance(config, SpawnConfig)
    assert config.cdp_url == "http://127.0.0.1:9222"
    assert config.env["BU_NAME"] == "ns1"
    assert "BROWSER_USE_API_KEY" not in config.env
    assert "BU_AUTOSPAWN" not in config.env


def test_build_spawn_config_refuses_without_cdp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harness = tmp_path / "browser-harness"
    harness.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    harness.chmod(harness.stat().st_mode | stat.S_IXUSR)
    monkeypatch.delenv("DREAM_CHROMIUM_CDP_URL", raising=False)
    monkeypatch.delenv("DREAM_CHROMIUM_CDP_WS", raising=False)
    reason = build_spawn_config(metadata={BROWSER_RUN_BIN_KEY: str(harness)}, bu_name="ns1")
    assert isinstance(reason, str)
    assert "Chromium CDP endpoint" in reason
