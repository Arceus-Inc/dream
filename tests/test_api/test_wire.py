"""Shared OpenAI-compatible wire helpers (reasoning-model token quirk)."""

from __future__ import annotations

import pytest

from dream.api._wire import apply_token_limit, is_reasoning_model, token_limit_param


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5", True),
        ("gpt-5-mini", True),
        ("o1", True),
        ("o3-mini", True),
        ("o4", True),
        ("openai/o3", True),  # gateway/route prefix tolerated
        ("gpt-4o", False),
        ("claude-sonnet-4-6", False),
    ],
)
def test_is_reasoning_model(model: str, expected: bool) -> None:
    assert is_reasoning_model(model) is expected


def test_token_limit_param_translates_for_reasoning_models() -> None:
    assert token_limit_param("o3", 100) == {"max_completion_tokens": 100}
    assert token_limit_param("gpt-4o", 100) == {"max_tokens": 100}


def test_apply_token_limit_renames_only_for_reasoning_models() -> None:
    assert apply_token_limit({"max_tokens": 50}, "o3") == {"max_completion_tokens": 50}
    # non-reasoning model: unchanged (same object)
    body = {"max_tokens": 50}
    assert apply_token_limit(body, "gpt-4o") is body
    # no max_tokens: unchanged
    assert apply_token_limit({"model": "o3"}, "o3") == {"model": "o3"}
