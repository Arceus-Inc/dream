"""Spec 04 stage 4a — token estimation against the substrate window.

The compactor needs a cheap, deterministic answer to two questions:

- *How many tokens does this transcript cost?* — answered by a char-heuristic
  walk over the typed block model (Spec 03's ``ConversationMessage``), padded
  by 4/3 so we err on the side of compacting too early rather than blowing the
  window.
- *What is the substrate's window, and have we crossed the threshold?* — read
  off ``ProviderCapabilities.max_context_tokens`` (Spec 02), with a configured
  default fallback when the substrate doesn't report one (acceptance #1).

Both answers are pure functions over the typed surfaces — no provider call,
no I/O — so the compaction trigger decision in ``_session.py``'s ``read``
boundary is itself pure.
"""

from __future__ import annotations

import pytest

from dream.contracts.provider import ProviderCapabilities
from dream.engine._messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.services.token_estimation import (
    DEFAULT_CONTEXT_WINDOW,
    TOKEN_ESTIMATION_PADDING,
    bounded_completion_tokens,
    estimate_conversation_tokens,
    estimate_tokens,
    resolve_context_window,
    should_auto_compact,
    utilisation,
)

# --- estimate_tokens ---------------------------------------------------------


def test_estimate_tokens_returns_zero_for_empty_string() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_uses_four_chars_per_token_heuristic() -> None:
    # 8 chars => 2 tokens by the (len + 3) // 4 heuristic.
    assert estimate_tokens("abcdefgh") == 2


def test_estimate_tokens_rounds_up_to_at_least_one() -> None:
    # A single character must still cost at least one token; this prevents
    # bursts of tiny blocks from underestimating to zero.
    assert estimate_tokens("a") == 1


def test_estimate_tokens_rounds_up_partial_token() -> None:
    # 5 chars => ceil(5/4) = 2 tokens.
    assert estimate_tokens("abcde") == 2


# --- estimate_conversation_tokens -------------------------------------------


def test_estimate_conversation_tokens_zero_for_empty_transcript() -> None:
    assert estimate_conversation_tokens([]) == 0


def test_estimate_conversation_tokens_walks_text_blocks() -> None:
    msg = ConversationMessage(role="user", content=[TextBlock(text="hello world!!")])
    # 13 chars text -> 4 tokens; 4 * 4/3 = 5 (int truncation).
    raw = estimate_tokens("hello world!!")
    expected = int(raw * TOKEN_ESTIMATION_PADDING)
    assert estimate_conversation_tokens([msg]) == expected


def test_estimate_conversation_tokens_counts_tool_result_content() -> None:
    """``ToolResultBlock.content`` is where most context bloat lives."""
    msg = ConversationMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id="t1", content="x" * 400)],
    )
    raw = estimate_tokens("x" * 400)
    expected = int(raw * TOKEN_ESTIMATION_PADDING)
    assert estimate_conversation_tokens([msg]) == expected


def test_estimate_conversation_tokens_counts_tool_use_name_and_input() -> None:
    msg = ConversationMessage(
        role="assistant",
        content=[
            ToolUseBlock(id="t1", name="read_file", input={"path": "/etc/hosts"}),
        ],
    )
    raw = estimate_tokens("read_file") + estimate_tokens(str({"path": "/etc/hosts"}))
    expected = int(raw * TOKEN_ESTIMATION_PADDING)
    assert estimate_conversation_tokens([msg]) == expected


def test_estimate_conversation_tokens_assigns_fixed_cost_per_image_block() -> None:
    """An ``ImageBlock`` is not a string; it carries a configured per-image cost."""
    text_only = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    with_image = [
        ConversationMessage(
            role="user",
            content=[
                TextBlock(text="hi"),
                ImageBlock(media_type="image/png", data="b64=="),
            ],
        )
    ]
    # The image cost MUST be both nonzero and significantly larger than a few
    # characters of text — otherwise we'd estimate images for free.
    diff = estimate_conversation_tokens(with_image) - estimate_conversation_tokens(text_only)
    assert diff >= 64


def test_estimate_conversation_tokens_includes_padding() -> None:
    """Aggregate result MUST be at least the raw block sum (4/3 padding > 1)."""
    msg = ConversationMessage(role="user", content=[TextBlock(text="abcd" * 100)])
    raw_total = estimate_tokens("abcd" * 100)
    assert estimate_conversation_tokens([msg]) >= raw_total


def test_estimate_conversation_tokens_grows_monotonically() -> None:
    """Adding a message MUST never decrease the estimate."""
    base = [ConversationMessage(role="user", content=[TextBlock(text="seed")])]
    grown = [
        *base,
        ConversationMessage(role="assistant", content=[TextBlock(text="reply")]),
    ]
    assert estimate_conversation_tokens(grown) >= estimate_conversation_tokens(base)


# --- resolve_context_window --------------------------------------------------


def test_resolve_context_window_uses_substrate_value() -> None:
    caps = ProviderCapabilities(max_context_tokens=128_000)
    window, used_fallback = resolve_context_window(caps)
    assert window == 128_000
    assert used_fallback is False


def test_resolve_context_window_falls_back_when_unreported() -> None:
    """Spec 04 acceptance #1: missing window MUST fall back to a default."""
    caps = ProviderCapabilities(max_context_tokens=None)
    window, used_fallback = resolve_context_window(caps)
    assert window == DEFAULT_CONTEXT_WINDOW
    assert used_fallback is True


def test_resolve_context_window_falls_back_when_capabilities_missing() -> None:
    """``None`` capabilities (e.g., before provider negotiation) MUST fall back."""
    window, used_fallback = resolve_context_window(None)
    assert window == DEFAULT_CONTEXT_WINDOW
    assert used_fallback is True


# --- utilisation -------------------------------------------------------------


def test_utilisation_is_zero_for_empty_transcript() -> None:
    caps = ProviderCapabilities(max_context_tokens=10_000)
    assert utilisation([], caps) == 0.0


def test_utilisation_is_ratio_of_estimate_to_window() -> None:
    caps = ProviderCapabilities(max_context_tokens=1_000)
    msg = ConversationMessage(role="user", content=[TextBlock(text="x" * 400)])
    expected = estimate_conversation_tokens([msg]) / 1_000
    assert utilisation([msg], caps) == pytest.approx(expected)


def test_utilisation_can_exceed_one_when_over_window() -> None:
    """Utilisation MUST NOT clip at 1.0 — the caller compares against thresholds."""
    caps = ProviderCapabilities(max_context_tokens=10)
    msg = ConversationMessage(role="user", content=[TextBlock(text="x" * 4_000)])
    assert utilisation([msg], caps) > 1.0


# --- should_auto_compact -----------------------------------------------------


def test_should_auto_compact_false_when_under_threshold() -> None:
    caps = ProviderCapabilities(max_context_tokens=100_000)
    msg = ConversationMessage(role="user", content=[TextBlock(text="hello")])
    assert should_auto_compact([msg], caps, threshold=0.7) is False


def test_should_auto_compact_true_when_over_threshold() -> None:
    caps = ProviderCapabilities(max_context_tokens=200)
    # ~150 raw chars -> ~38 raw tokens -> 50 padded; window 200 -> 0.25
    # so we set a very low threshold to force True deterministically.
    msg = ConversationMessage(role="user", content=[TextBlock(text="x" * 150)])
    assert should_auto_compact([msg], caps, threshold=0.1) is True


def test_should_auto_compact_default_threshold_is_seventy_percent() -> None:
    """Spec 04 #1: ``Default auto threshold = 70% of the window``."""
    caps = ProviderCapabilities(max_context_tokens=1_000)
    # Build a transcript whose utilisation lands between 0.69 and 0.71, then
    # assert the default-threshold branch matches the >= 0.7 view of it.
    msg = ConversationMessage(role="user", content=[TextBlock(text="x" * 2_200)])
    u = utilisation([msg], caps)
    assert should_auto_compact([msg], caps) is (u >= 0.7)


# --- bounded_completion_tokens ----------------------------------------------


def test_bounded_completion_tokens_returns_requested_when_room_available() -> None:
    assert bounded_completion_tokens(4_000, window=100_000, used=1_000) == 4_000


def test_bounded_completion_tokens_caps_to_remaining_window() -> None:
    """Spec 04 #1: never request a completion the window can't hold."""
    # 1000 used, window 5000 -> 4000 available; requested 8000 -> capped at 4000.
    assert bounded_completion_tokens(8_000, window=5_000, used=1_000) == 4_000


def test_bounded_completion_tokens_respects_safety_margin() -> None:
    """A safety margin reserves headroom even within the formal remainder."""
    # 1000 used, window 5000, margin 500 -> 3500 effective available.
    assert bounded_completion_tokens(8_000, window=5_000, used=1_000, safety_margin=500) == 3_500


def test_bounded_completion_tokens_never_negative() -> None:
    """An already-over-budget transcript MUST yield zero, not a negative cap."""
    assert bounded_completion_tokens(4_000, window=1_000, used=2_000) == 0


def test_bounded_completion_tokens_never_negative_with_margin() -> None:
    """Margin MUST NOT push the cap below zero."""
    assert (
        bounded_completion_tokens(4_000, window=1_000, used=500, safety_margin=900)
        == 0
    )
