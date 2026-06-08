"""Spec 13D.1 — hard per-session limit counters.

A fresh SessionLimiter per session (no roll-forward); each counter trips at its
cap; breached() returns the tripped counter's limit-exceeded label. Tokens and
tool-calls are wired into the loop (13D.2); the network counter's API exists but
is auto-incremented only once the egress layer lands (leftover #03).
"""

from __future__ import annotations

import pytest

from dream.permissions import SessionLimiter, SessionLimits


def test_defaults_match_spec() -> None:
    limits = SessionLimits()
    assert limits.max_llm_tokens == 5_000_000
    assert limits.max_tool_calls == 2_000
    assert limits.max_network_calls == 500


def test_fresh_limiter_is_not_breached() -> None:
    assert SessionLimiter().breached() is None


def test_tokens_trip_at_cap() -> None:
    lim = SessionLimiter(SessionLimits(max_llm_tokens=100))
    lim.record_tokens(99)
    assert lim.breached() is None
    lim.record_tokens(1)
    assert lim.breached() == "limit-exceeded:llm_tokens"


def test_tool_calls_trip_at_cap() -> None:
    lim = SessionLimiter(SessionLimits(max_tool_calls=2))
    lim.record_tool_call()
    assert lim.breached() is None
    lim.record_tool_call()
    assert lim.breached() == "limit-exceeded:tool_calls"


def test_network_calls_trip_at_cap() -> None:
    lim = SessionLimiter(SessionLimits(max_network_calls=1))
    assert lim.breached() is None
    lim.record_network_call()
    assert lim.breached() == "limit-exceeded:network_calls"


def test_tokens_accumulate_across_calls() -> None:
    lim = SessionLimiter(SessionLimits(max_llm_tokens=10))
    lim.record_tokens(4)
    lim.record_tokens(4)
    assert lim.breached() is None
    lim.record_tokens(4)
    assert lim.breached() == "limit-exceeded:llm_tokens"


def test_tokens_checked_before_tool_calls() -> None:
    lim = SessionLimiter(SessionLimits(max_llm_tokens=1, max_tool_calls=1))
    lim.record_tokens(5)
    lim.record_tool_call()
    assert lim.breached() == "limit-exceeded:llm_tokens"


def test_negative_token_count_rejected() -> None:
    with pytest.raises(ValueError):
        SessionLimiter().record_tokens(-1)


def test_no_roll_forward_between_limiters() -> None:
    first = SessionLimiter(SessionLimits(max_tool_calls=1))
    first.record_tool_call()
    assert first.breached() is not None
    second = SessionLimiter(SessionLimits(max_tool_calls=1))
    assert second.breached() is None
