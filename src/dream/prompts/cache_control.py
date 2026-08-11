"""Hermes-style prompt-cache breakpoints for OpenAI-compatible chat turns.

Providers that honour ``cache_control`` get up to four ephemeral markers:
optional static ``<stable>`` prefix, remainder of the system message, and the
last N non-system messages. Providers that ignore unknown fields are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CacheControl",
    "CacheTtl",
    "OpenAIChatMessage",
    "OpenAIFunctionCall",
    "OpenAIToolCall",
    "StableSystemSplit",
    "TextContentBlock",
    "apply_cache_control",
    "split_stable_system_prefix",
]

_MAX_BREAKPOINTS = 4
_STABLE_OPEN = "<stable>"
_STABLE_CLOSE = "</stable>"


class CacheTtl(StrEnum):
    """Provider TTL for an ephemeral cache breakpoint."""

    FIVE_MINUTES = "5m"
    ONE_HOUR = "1h"


@dataclass(frozen=True, slots=True)
class CacheControl:
    """One ephemeral cache breakpoint."""

    ttl: CacheTtl = CacheTtl.FIVE_MINUTES

    def to_wire(self) -> Mapping[str, str]:
        if self.ttl is CacheTtl.ONE_HOUR:
            return {"type": "ephemeral", "ttl": self.ttl.value}
        return {"type": "ephemeral"}


@dataclass(frozen=True, slots=True)
class TextContentBlock:
    """A text content part, optionally carrying a cache breakpoint."""

    text: str
    cache_control: CacheControl | None = None

    def to_wire(self) -> Mapping[str, object]:
        wire: dict[str, object] = {"type": "text", "text": self.text}
        if self.cache_control is not None:
            wire["cache_control"] = dict(self.cache_control.to_wire())
        return wire

    def with_cache(self, marker: CacheControl) -> TextContentBlock:
        return TextContentBlock(text=self.text, cache_control=marker)


@dataclass(frozen=True, slots=True)
class OpenAIFunctionCall:
    """OpenAI ``tool_calls[].function`` payload."""

    name: str
    arguments: str

    def to_wire(self) -> Mapping[str, str]:
        return {"name": self.name, "arguments": self.arguments}


@dataclass(frozen=True, slots=True)
class OpenAIToolCall:
    """One assistant tool call on the OpenAI wire."""

    id: str
    function: OpenAIFunctionCall

    def to_wire(self) -> Mapping[str, object]:
        return {
            "id": self.id,
            "type": "function",
            "function": dict(self.function.to_wire()),
        }


@dataclass(frozen=True, slots=True)
class OpenAIChatMessage:
    """One OpenAI chat message before JSON serialization."""

    role: str
    content: str | tuple[TextContentBlock, ...] | None = None
    tool_calls: tuple[OpenAIToolCall, ...] | None = None
    tool_call_id: str | None = None

    def to_wire(self) -> Mapping[str, object]:
        wire: dict[str, object] = {"role": self.role}
        if self.tool_call_id is not None:
            wire["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            wire["tool_calls"] = [call.to_wire() for call in self.tool_calls]
        if self.content is None and self.tool_calls is not None:
            wire["content"] = None
        elif isinstance(self.content, tuple):
            wire["content"] = [block.to_wire() for block in self.content]
        elif self.content is not None:
            wire["content"] = self.content
        return wire

    def with_tail_cache(self, marker: CacheControl) -> OpenAIChatMessage:
        """Attach ``marker`` to the last text block (promoting string content if needed)."""
        if self.content is None or self.content == "":
            return self
        if isinstance(self.content, str):
            return OpenAIChatMessage(
                role=self.role,
                content=(TextContentBlock(text=self.content, cache_control=marker),),
                tool_calls=self.tool_calls,
                tool_call_id=self.tool_call_id,
            )
        if not self.content:
            return self
        head, last = self.content[:-1], self.content[-1]
        return OpenAIChatMessage(
            role=self.role,
            content=(*head, last.with_cache(marker)),
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
        )


@dataclass(frozen=True, slots=True)
class StableSystemSplit:
    """``<stable>...</stable>`` prefix extracted for a dedicated cache breakpoint."""

    prefix: str | None
    prompt: str


def split_stable_system_prefix(system_prompt: str) -> StableSystemSplit:
    """Return the leading ``<stable>`` block when present; otherwise prefix is unset."""
    start = system_prompt.find(_STABLE_OPEN)
    end = system_prompt.find(_STABLE_CLOSE)
    if start < 0 or end < 0 or end <= start:
        return StableSystemSplit(prefix=None, prompt=system_prompt)
    close_at = end + len(_STABLE_CLOSE)
    return StableSystemSplit(prefix=system_prompt[:close_at].strip(), prompt=system_prompt)


def apply_cache_control(
    messages: Sequence[OpenAIChatMessage],
    *,
    static_system_prefix: str | None = None,
    ttl: CacheTtl = CacheTtl.FIVE_MINUTES,
    max_message_breakpoints: int = 2,
) -> tuple[OpenAIChatMessage, ...]:
    """Return messages with ≤4 Hermes-style cache breakpoints applied."""
    if not messages:
        return ()
    marker = CacheControl(ttl=ttl)
    out = list(messages)
    used = 0

    if out[0].role == "system":
        out[0], used = _mark_system(out[0], marker, static_system_prefix)

    remaining = max(0, _MAX_BREAKPOINTS - used)
    budget = min(max_message_breakpoints, remaining)
    if budget == 0:
        return tuple(out)

    carriers = [index for index, message in enumerate(out) if _can_carry(message)]
    for index in carriers[-budget:]:
        out[index] = out[index].with_tail_cache(marker)
    return tuple(out)


def _mark_system(
    message: OpenAIChatMessage,
    marker: CacheControl,
    static_prefix: str | None,
) -> tuple[OpenAIChatMessage, int]:
    text = _string_content(message)
    if text is None or text == "":
        return message, 0
    if (
        static_prefix
        and text.startswith(static_prefix)
        and text != static_prefix
    ):
        suffix = text[len(static_prefix) :].lstrip("\n")
        marked = OpenAIChatMessage(
            role=message.role,
            content=(
                TextContentBlock(text=static_prefix, cache_control=marker),
                TextContentBlock(text=suffix, cache_control=marker),
            ),
        )
        return marked, 2
    marked = OpenAIChatMessage(
        role=message.role,
        content=(TextContentBlock(text=text, cache_control=marker),),
    )
    return marked, 1


def _string_content(message: OpenAIChatMessage) -> str | None:
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, tuple):
        return "".join(block.text for block in message.content)
    return None


def _can_carry(message: OpenAIChatMessage) -> bool:
    if message.role == "system":
        return False
    if message.content is None or message.content == "":
        return False
    if isinstance(message.content, tuple):
        return bool(message.content)
    return True
