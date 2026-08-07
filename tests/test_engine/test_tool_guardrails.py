"""Unit tests for the exact-failure tool circuit breaker."""

from __future__ import annotations

from dream.engine._tool_guardrails import GuardrailVerdict, ToolGuardrails, fingerprint_args


def test_fingerprint_is_order_independent() -> None:
    assert fingerprint_args({"b": 1, "a": 2}) == fingerprint_args({"a": 2, "b": 1})


def test_exact_failure_warns_then_blocks() -> None:
    rails = ToolGuardrails(warn_after=2, block_after=3)
    fp = fingerprint_args({"path": "x"})
    assert (
        rails.observe_error(tool="bash", args_fingerprint=fp, error_key="e")
        is GuardrailVerdict.ALLOW
    )
    assert (
        rails.observe_error(tool="bash", args_fingerprint=fp, error_key="e")
        is GuardrailVerdict.WARN
    )
    assert (
        rails.observe_error(tool="bash", args_fingerprint=fp, error_key="e")
        is GuardrailVerdict.BLOCK
    )


def test_success_clears_streak() -> None:
    rails = ToolGuardrails(warn_after=2, block_after=3)
    fp = fingerprint_args({"path": "x"})
    rails.observe_error(tool="bash", args_fingerprint=fp, error_key="e")
    rails.observe_error(tool="bash", args_fingerprint=fp, error_key="e")
    rails.observe_success(tool="bash", args_fingerprint=fp)
    assert (
        rails.observe_error(tool="bash", args_fingerprint=fp, error_key="e")
        is GuardrailVerdict.ALLOW
    )


def test_different_args_are_independent() -> None:
    rails = ToolGuardrails(warn_after=2, block_after=2)
    a = fingerprint_args({"n": 1})
    b = fingerprint_args({"n": 2})
    assert (
        rails.observe_error(tool="bash", args_fingerprint=a, error_key="e")
        is GuardrailVerdict.ALLOW
    )
    assert (
        rails.observe_error(tool="bash", args_fingerprint=b, error_key="e")
        is GuardrailVerdict.ALLOW
    )
    assert (
        rails.observe_error(tool="bash", args_fingerprint=a, error_key="e")
        is GuardrailVerdict.BLOCK
    )
