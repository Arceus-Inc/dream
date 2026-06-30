"""Unit tests for dream.api._timeout — deadline primitives.

Covers Deadline.of(), call_deadline(), current_deadline(), push_deadline(),
and SubstrateTimeout hierarchy.
"""

from __future__ import annotations

import threading

from dream.api._timeout import (
    DEFAULT_TIMEOUT_SECONDS,
    Deadline,
    SubstrateTimeout,
    call_deadline,
    current_deadline,
    push_deadline,
)

# --- SubstrateTimeout (lines 24-29) ---


def test_substrate_timeout_is_timeout_error() -> None:
    assert issubclass(SubstrateTimeout, TimeoutError)


def test_substrate_timeout_carries_message() -> None:
    exc = SubstrateTimeout("request timed out after 60s")
    assert "60s" in str(exc)


# --- Deadline (lines 32-40) ---


def test_deadline_of_with_value() -> None:
    d = Deadline.of(30.0)
    assert d.seconds == 30.0


def test_deadline_of_with_none_uses_default() -> None:
    d = Deadline.of(None)
    assert d.seconds == DEFAULT_TIMEOUT_SECONDS


def test_deadline_of_converts_int_to_float() -> None:
    d = Deadline.of(45)
    assert d.seconds == 45.0
    assert isinstance(d.seconds, float)


def test_deadline_is_frozen() -> None:
    d = Deadline.of(30.0)
    try:
        d.seconds = 60.0  # type: ignore[misc]
        assert False, "should have raised"
    except AttributeError:
        pass


# --- call_deadline (lines 43-52) ---


def test_call_deadline_yields_deadline() -> None:
    with call_deadline(10.0) as d:
        assert isinstance(d, Deadline)
        assert d.seconds == 10.0


def test_call_deadline_none_uses_default() -> None:
    with call_deadline(None) as d:
        assert d.seconds == DEFAULT_TIMEOUT_SECONDS


def test_call_deadline_default() -> None:
    with call_deadline() as d:
        assert d.seconds == DEFAULT_TIMEOUT_SECONDS


# --- current_deadline (lines 58-65) ---


def test_current_deadline_none_by_default() -> None:
    # Run in a fresh thread to ensure no leaked state from other tests.
    result: list[Deadline | None] = [None]

    def _check() -> None:
        result[0] = current_deadline()

    t = threading.Thread(target=_check)
    t.start()
    t.join()
    assert result[0] is None


# --- push_deadline (lines 68-79) ---


def test_push_deadline_sets_and_restores() -> None:
    result_inside: list[Deadline | None] = [None]
    result_outside: list[Deadline | None] = [None]

    def _check() -> None:
        result_outside[0] = current_deadline()
        d = Deadline.of(42.0)
        with push_deadline(d) as pushed:
            result_inside[0] = current_deadline()
            assert pushed is d
        result_outside[0] = current_deadline()

    t = threading.Thread(target=_check)
    t.start()
    t.join()
    assert result_inside[0] is not None
    assert result_inside[0].seconds == 42.0
    assert result_outside[0] is None


def test_push_deadline_nested() -> None:
    results: list[float | None] = []

    def _append_seconds() -> None:
        dl = current_deadline()
        results.append(dl.seconds if dl else None)

    def _check() -> None:
        d1 = Deadline.of(10.0)
        d2 = Deadline.of(20.0)
        with push_deadline(d1):
            _append_seconds()
            with push_deadline(d2):
                _append_seconds()
            _append_seconds()
        _append_seconds()

    t = threading.Thread(target=_check)
    t.start()
    t.join()
    assert results == [10.0, 20.0, 10.0, None]
