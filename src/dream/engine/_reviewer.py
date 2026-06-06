"""Reviewer (Ralph-Wiggum) loop primitives (Spec 03 stage 3b, acceptance #13).

A ``Reviewer`` is a read-only subagent the session consults before
declaring ``done``. It returns a ``ReviewerOutcome`` whose ``verdict``
is either ``"accept"`` (seal as ``done``) or ``"request_changes"``
(re-enter ``working`` with the items injected as a user message).
After ``max_rounds`` consecutive request_changes verdicts the
orchestrator force-closes as ``done-with-warnings``.

The reviewer's tooling, prompt, and substrate live in spec #06/#10;
this module only pins the verdict shape and the protocol the
orchestrator programs against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from dream.engine._messages import ConversationMessage, TextBlock

ReviewerVerdict = Literal["accept", "request_changes"]


@dataclass(frozen=True)
class ReviewerOutcome:
    verdict: ReviewerVerdict
    # Tuple, not list: a frozen verdict must not be mutable after the reviewer
    # returns it (``outcome.items.append(...)`` on a frozen record is a footgun).
    items: tuple[str, ...] = ()

    def to_user_message(self) -> ConversationMessage:
        if self.items:
            bullets = "\n".join(f"- {it}" for it in self.items)
            text = f"Reviewer requested changes:\n{bullets}"
        else:
            text = "Reviewer requested changes (no specific items)."
        return ConversationMessage(role="user", content=[TextBlock(text=text)])


class Reviewer(Protocol):
    async def review(
        self, transcript: list[ConversationMessage]
    ) -> ReviewerOutcome: ...


@dataclass
class ReviewerConfig:
    reviewer: Reviewer
    max_rounds: int = 3


__all__ = [
    "Reviewer",
    "ReviewerConfig",
    "ReviewerOutcome",
    "ReviewerVerdict",
]
