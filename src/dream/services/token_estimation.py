"""Spec 04 stage 4a — token estimation against the substrate window.

Pure functions over Spec 03's typed transcript and Spec 02's provider
capabilities — no provider call, no I/O. The compactor (stage 4b) and the
act-loop's ``read`` boundary (stage 4c) both rely on these to decide
*whether* to compact and *how big* a completion to request, without taking
a model round-trip.

Two answers, both deterministic:

- *How many tokens does this transcript cost?* — a 4-chars-per-token
  heuristic walk over the typed block model, padded by 4/3 so we err on
  the side of compacting too early rather than blowing the window.
- *What is the substrate's window, and have we crossed the threshold?* —
  read off ``ProviderCapabilities.max_context_tokens`` with a configured
  default fallback when the substrate doesn't report one (Spec 04 #1).

Borrowed from OpenHarness's ``services/token_estimation.py`` +
``services/compact/__init__.py``; env-var prefix is ``DREAM_`` to match
the rest of this codebase.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from dream.contracts.provider import ProviderCapabilities
from dream.engine._messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

# --- Constants ---------------------------------------------------------------

# Per Spec 04 #1: substrate-reported window with a configured default fallback.
DEFAULT_CONTEXT_WINDOW: int = 200_000

# 4/3 padding: char-count heuristics under-estimate; bias toward compacting too
# early rather than blowing the window.
TOKEN_ESTIMATION_PADDING: float = 4 / 3

# Per-image cost when an ``ImageBlock`` is present. Substantially larger than a
# few characters of text so vision content is never counted as free.
DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE: int = 3_072

# --- Token estimation --------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Estimate tokens from plain text using a rough char heuristic.

    Empty string costs zero; any non-empty string costs at least one token so
    a burst of tiny blocks can't silently underestimate to zero.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _vision_token_budget_per_image() -> int:
    # Bad env values silently fall back to the default — spec 00 rule 4 forbids
    # logging here, and the caller has no actionable response to a typo anyway.
    raw = os.environ.get("DREAM_IMAGE_TOKEN_ESTIMATE", "").strip()
    if raw:
        try:
            return max(64, int(raw))
        except ValueError:
            return DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE
    return DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE


def estimate_conversation_tokens(messages: Sequence[ConversationMessage]) -> int:
    """Estimate total token cost of a conversation, with 4/3 padding applied."""
    total = 0
    image_token_estimate = _vision_token_budget_per_image()
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                total += estimate_tokens(block.text)
            elif isinstance(block, ToolResultBlock):
                total += estimate_tokens(block.content)
            elif isinstance(block, ToolUseBlock):
                total += estimate_tokens(block.name)
                total += estimate_tokens(str(block.input))
            elif isinstance(block, ImageBlock):
                total += image_token_estimate
    return int(total * TOKEN_ESTIMATION_PADDING)


# --- Context window ----------------------------------------------------------


def resolve_context_window(
    capabilities: ProviderCapabilities | None,
) -> tuple[int, bool]:
    """Return ``(window, used_fallback)``.

    Substrate-reported value is preferred when present; otherwise we fall back
    to ``DEFAULT_CONTEXT_WINDOW`` and signal the fallback so the act-loop can
    surface a hint that compaction maths is approximate.
    """
    if capabilities is not None and capabilities.max_context_tokens:
        return capabilities.max_context_tokens, False
    return DEFAULT_CONTEXT_WINDOW, True


def utilisation(
    messages: Sequence[ConversationMessage],
    capabilities: ProviderCapabilities | None,
) -> float:
    """Return token-estimate / window as a fraction. May exceed 1.0."""
    window, _ = resolve_context_window(capabilities)
    if window <= 0:  # defensive — a zero window would be a substrate bug
        return 0.0
    return estimate_conversation_tokens(messages) / window


def should_auto_compact(
    messages: Sequence[ConversationMessage],
    capabilities: ProviderCapabilities | None,
    *,
    threshold: float = 0.7,
) -> bool:
    """True iff utilisation has crossed the auto-compact threshold (default 70%)."""
    return utilisation(messages, capabilities) >= threshold


# --- Completion-budget bound -------------------------------------------------


def bounded_completion_tokens(
    requested: int,
    *,
    window: int,
    used: int,
    safety_margin: int = 0,
) -> int:
    """Cap a requested completion budget by what the window can still hold.

    Never returns a negative value — an already-over-budget transcript yields
    0 so the caller can detect "no room" without special-casing sign.
    """
    available = window - used - safety_margin
    return max(0, min(requested, available))


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE",
    "TOKEN_ESTIMATION_PADDING",
    "bounded_completion_tokens",
    "estimate_conversation_tokens",
    "estimate_tokens",
    "resolve_context_window",
    "should_auto_compact",
    "utilisation",
]
