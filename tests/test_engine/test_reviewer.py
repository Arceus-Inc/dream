"""Spec 03 stage 3b — reviewer (Ralph-Wiggum) primitives.

Pins the verdict + outcome shapes and the ``Reviewer`` protocol surface.
Session-level integration (re-entry on request_changes, max-rounds
force-close) is exercised in ``test_session_rituals.py``; here we just
nail the static contracts the orchestrator will program against.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dream.engine._messages import ConversationMessage, TextBlock
from dream.engine._reviewer import (
    Reviewer,
    ReviewerConfig,
    ReviewerOutcome,
)

# --- ReviewerOutcome --------------------------------------------------------


def test_outcome_accept_carries_no_items_by_default() -> None:
    out = ReviewerOutcome(verdict="accept")
    assert out.verdict == "accept"
    assert out.items == []


def test_outcome_request_changes_carries_items() -> None:
    out = ReviewerOutcome(
        verdict="request_changes",
        items=["fix the typo", "address edge case"],
    )
    assert out.verdict == "request_changes"
    assert out.items == ["fix the typo", "address edge case"]


def test_outcome_is_frozen() -> None:
    out = ReviewerOutcome(verdict="accept")
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(out, "verdict", "request_changes")


def test_outcome_to_user_message_renders_items_as_user_role_textblock() -> None:
    """The orchestrator will inject this on every request_changes round."""
    out = ReviewerOutcome(
        verdict="request_changes", items=["fix A", "fix B"]
    )
    msg = out.to_user_message()
    assert isinstance(msg, ConversationMessage)
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextBlock)
    text = msg.text
    assert "fix A" in text
    assert "fix B" in text


def test_outcome_to_user_message_with_no_items_still_renders_a_message() -> None:
    out = ReviewerOutcome(verdict="request_changes")
    msg = out.to_user_message()
    # No items but should not crash; some non-empty body so the model
    # has a turn to respond to.
    assert msg.text.strip() != ""


# --- Reviewer protocol structural conformance --------------------------------


async def test_reviewer_protocol_is_satisfied_structurally() -> None:
    class Concrete:
        async def review(
            self, transcript: list[ConversationMessage]
        ) -> ReviewerOutcome:
            return ReviewerOutcome(verdict="accept")

    r: Reviewer = Concrete()
    out = await r.review([])
    assert out.verdict == "accept"


# --- ReviewerConfig ---------------------------------------------------------


def test_reviewer_config_holds_reviewer_and_max_rounds() -> None:
    class Stub:
        async def review(
            self, transcript: list[ConversationMessage]
        ) -> ReviewerOutcome:
            return ReviewerOutcome(verdict="accept")

    stub = Stub()
    cfg = ReviewerConfig(reviewer=stub, max_rounds=5)
    assert cfg.reviewer is stub
    assert cfg.max_rounds == 5


def test_reviewer_config_max_rounds_defaults_to_three() -> None:
    class Stub:
        async def review(
            self, transcript: list[ConversationMessage]
        ) -> ReviewerOutcome:
            return ReviewerOutcome(verdict="accept")

    cfg = ReviewerConfig(reviewer=Stub())
    assert cfg.max_rounds == 3
