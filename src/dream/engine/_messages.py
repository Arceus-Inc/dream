"""Internal transcript types + tool-call atom enforcement (Spec 03 stage 1).

The transcript is modelled as a list of ``ConversationMessage``s, each carrying
a ``list[ContentBlock]`` rather than a raw string. That typing is what makes
Spec 00 invariant #1 — *the tool-call atom* — mechanically enforceable: the
engine can find dangling ``ToolUseBlock``s structurally instead of by
string-matching the provider's output.

This module is internal: nothing here is re-exported from ``dream/__init__.py``.
The two pure functions ``sanitize_conversation_messages`` and
``has_pending_continuation`` are the only safety rail between an interrupted
turn and the next provider call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# --- content blocks ----------------------------------------------------------


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ImageBlock:
    media_type: str
    data: str


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock

Role = Literal["user", "assistant"]


# --- message -----------------------------------------------------------------


@dataclass
class ConversationMessage:
    role: Role
    content: list[ContentBlock]

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def tool_results(self) -> list[ToolResultBlock]:
        return [b for b in self.content if isinstance(b, ToolResultBlock)]

    def is_effectively_empty(self) -> bool:
        """True iff this message would add nothing meaningful to the transcript.

        Empty content, or content that is only blank/whitespace text. Any
        tool_use, tool_result, image, or non-blank text makes the message
        substantive.
        """
        for block in self.content:
            if isinstance(block, TextBlock):
                if block.text.strip():
                    return False
            else:
                return False
        return True


def assistant_message_from_api(blocks: Sequence[ContentBlock]) -> ConversationMessage:
    """Wrap a parsed provider response into a ``ConversationMessage``.

    The single builder the engine uses after parsing a provider turn so the
    transcript shape stays in one place.
    """
    return ConversationMessage(role="assistant", content=list(blocks))


# --- tool-call atom enforcement ----------------------------------------------


def sanitize_conversation_messages(
    messages: Sequence[ConversationMessage],
) -> list[ConversationMessage]:
    """Return a transcript safe to send to a provider.

    Two repairs, in order:

    1. Drop effectively-empty assistant messages anywhere in the transcript.
       These come from legacy/partial provider turns and break OpenAI-style
       APIs that reject empty content.
    2. Trim any trailing assistant message carrying a ``ToolUseBlock`` — the
       canonical interrupted-mid-turn corruption. By construction the last
       message has no following ``tool_result``, so any tool_use in the tail
       assistant message is unmatched and the whole message must go (mixed-in
       text goes with it; the unanswered tool_use poisons the turn).

    The input is never mutated; the result is a new list. The function is
    idempotent — running it twice produces the same result.
    """
    out = [
        m for m in messages
        if not (m.role == "assistant" and m.is_effectively_empty())
    ]
    while out and out[-1].role == "assistant" and out[-1].tool_uses:
        out.pop()
    return out


def has_pending_continuation(messages: Sequence[ConversationMessage]) -> bool:
    """True iff the transcript ends in user ``tool_result``s owing a model turn.

    The crash-resume entry point: a transcript whose last message is a user
    message carrying any ``ToolResultBlock`` represents a tool round whose
    results are in but whose model turn was never produced. On resume the
    engine must re-enter the model to consume those results rather than
    treating the prior turn as complete.
    """
    if not messages:
        return False
    last = messages[-1]
    return last.role == "user" and bool(last.tool_results)


__all__ = [
    "ContentBlock",
    "ConversationMessage",
    "ImageBlock",
    "Role",
    "TextBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "assistant_message_from_api",
    "has_pending_continuation",
    "sanitize_conversation_messages",
]
