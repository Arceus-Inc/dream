"""Spec 03 stage 3c - OpenAI-compatible ``TurnStreamer`` adapter.

This is the engine-facing adapter (vs. the Spec 02 ``OpenAIChatSubstrate``
in ``dream.api.openai`` which models the single-prompt substrate
Protocol). The engine drives the act-loop through the typed
``TurnStreamer`` Protocol in ``_loop.py``; this module wires that
Protocol to the OpenAI Chat Completions streaming wire format.

The adapter has two seams:

- A ``stream_chat_completion(messages, model)`` async callable that
  returns an ``AsyncIterator[dict]`` of raw OpenAI chunks. Tests inject
  a scripted iterator; production uses ``httpx_chat_completion_stream``
  below which speaks raw SSE against any OpenAI-compatible endpoint
  (LiteLLM, vLLM, Azure ``/openai/v1``, vanilla OpenAI, etc.).
- A ``conversation_to_openai_messages`` pure function that translates
  the engine's typed transcript into OpenAI's wire-format dicts,
  splitting ``ToolResultBlock``s into ``tool`` role messages and
  collapsing assistant ``ToolUseBlock``s into ``tool_calls``.

Nothing here is logged: errors are surfaced as ``ErrorEvent`` or raised
directly so the heartbeat aborts the session per Spec 00 invariants.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from dream.api._wire import apply_token_limit
from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StreamEvent,
)
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

StreamChatCompletion = Callable[
    [Sequence[dict[str, Any]], str],
    Awaitable[AsyncIterator[dict[str, Any]]],
]


# --- transcript translation -------------------------------------------------


def conversation_to_openai_messages(
    messages: Sequence[ConversationMessage],
    *,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Translate the engine transcript into OpenAI Chat Completions messages.

    Mapping rules:

    - ``system_prompt`` (if any) is prepended as ``{"role": "system", ...}``.
    - An assistant message with one or more ``ToolUseBlock``s collapses to a
      single ``{"role": "assistant", "content": <text|None>, "tool_calls":
      [...]}`` (text is the concatenation of any leading ``TextBlock``s; the
      ``arguments`` field is JSON-serialised per the wire format).
    - A user message that contains ``ToolResultBlock``s splits: each
      tool-result becomes a ``{"role": "tool", "tool_call_id": ..., "content":
      ...}`` message in original order; any remaining ``TextBlock`` text is
      flushed as a final ``{"role": "user", "content": ...}`` message after
      the tool messages.
    - Plain user/assistant text becomes the obvious string-content message.

    ``ImageBlock``s are not yet mapped; they pass through as part of the
    text content concatenation (callers that need vision should provide
    their own translation in a later stage).
    """
    out: list[dict[str, Any]] = []
    if system_prompt is not None:
        out.append({"role": "system", "content": system_prompt})

    for msg in messages:
        if msg.role == "assistant":
            out.extend(_translate_assistant(msg))
        else:
            out.extend(_translate_user(msg))

    return out


def _translate_assistant(msg: ConversationMessage) -> list[dict[str, Any]]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                }
            )

    text = "".join(text_parts)
    if tool_calls:
        return [
            {
                "role": "assistant",
                "content": text if text else None,
                "tool_calls": tool_calls,
            }
        ]
    return [{"role": "assistant", "content": text}]


def _translate_user(msg: ConversationMessage) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in msg.content:
        if isinstance(block, ToolResultBlock):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": block.content,
                }
            )
        elif isinstance(block, TextBlock):
            text_parts.append(block.text)
    text = "".join(text_parts)
    if text:
        out.append({"role": "user", "content": text})
    return out


# --- streaming adapter ------------------------------------------------------


@dataclass
class _ToolCallAccumulator:
    id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAIChatStreamer:
    """``TurnStreamer`` adapter for any OpenAI-compatible Chat Completions API.

    Construction takes a ``stream_chat_completion`` callable (see module
    docstring) plus the model name. ``system_prompt`` is optional and
    prepended to every translated transcript. The adapter is stateless
    across ``stream_turn`` calls.
    """

    def __init__(
        self,
        *,
        stream_chat_completion: StreamChatCompletion,
        model: str,
        system_prompt: str | None = None,
    ) -> None:
        self._stream_chat = stream_chat_completion
        self._model = model
        self._system_prompt = system_prompt

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        wire = conversation_to_openai_messages(
            messages, system_prompt=self._system_prompt
        )
        chunks = await self._stream_chat(wire, self._model)
        # Own the transport stream's lifecycle: ``aclosing`` releases the httpx
        # connection (in ``httpx_chat_completion_stream``) whenever this turn is
        # closed — including an outer cancel/timeout that closes us mid-stream.
        async with contextlib.aclosing(
            cast(AsyncGenerator[dict[str, Any], None], chunks)
        ):
            async for ev in self._consume(chunks):
                yield ev

    async def _consume(
        self, chunks: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[StreamEvent]:
        text_parts: list[str] = []
        tool_calls: dict[int, _ToolCallAccumulator] = {}
        usage: UsageSnapshot = UsageSnapshot()

        async for chunk in chunks:
            usage_payload = chunk.get("usage")
            if usage_payload:
                usage = _usage_from_payload(usage_payload)

            for choice in chunk.get("choices") or ():
                delta = choice.get("delta") or {}

                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield AssistantTextDelta(text=content)

                for tc in delta.get("tool_calls") or ():
                    _merge_tool_call(tool_calls, tc)

        blocks: list[ContentBlock] = []
        text = "".join(text_parts)
        if text:
            blocks.append(TextBlock(text=text))
        for idx in sorted(tool_calls):
            acc = tool_calls[idx]
            if not acc.id or not acc.name:
                # A truncated stream can leave an accumulator with no id/name.
                # Emitting ToolUseBlock(id="", name="") would collide under
                # id-keyed result matching — surface and drop it instead.
                yield ErrorEvent(
                    message=(
                        f"dropping incomplete tool call (id={acc.id!r}, "
                        f"name={acc.name!r}): the stream ended mid-call"
                    ),
                    recoverable=True,
                )
                continue
            if acc.arguments:
                try:
                    args = json.loads(acc.arguments)
                except json.JSONDecodeError as exc:
                    # Don't fabricate empty args and dispatch a tool the model
                    # never actually parameterised — that risks invoking a
                    # privileged tool with unintended defaults. Surface the
                    # corruption and drop this call instead.
                    yield ErrorEvent(
                        message=(
                            f"tool call {acc.name!r} (id={acc.id!r}) had unparseable "
                            f"JSON arguments; dropping it: {exc}"
                        ),
                        recoverable=True,
                    )
                    continue
            else:
                args = {}
            blocks.append(ToolUseBlock(id=acc.id, name=acc.name, input=args))

        yield AssistantTurnComplete(blocks=blocks, usage=usage)


def _merge_tool_call(
    acc: dict[int, _ToolCallAccumulator], partial: dict[str, Any]
) -> None:
    try:
        idx = int(partial.get("index", 0))
    except (TypeError, ValueError):
        # A non-numeric index is a protocol violation from the wire; skip this
        # fragment rather than letting int() abort the whole turn.
        return
    entry = acc.setdefault(idx, _ToolCallAccumulator())
    if partial.get("id"):
        entry.id = partial["id"]
    fn = partial.get("function") or {}
    if fn.get("name"):
        entry.name = fn["name"]
    if "arguments" in fn and fn["arguments"] is not None:
        entry.arguments += fn["arguments"]


def _usage_from_payload(payload: dict[str, Any]) -> UsageSnapshot:
    # ``cache_write_tokens`` is intentionally left at 0: OpenAI's wire format has
    # no cache-*write* counter (prompt caching is automatic and reports only
    # ``cached_tokens`` = cache *reads*). It's a UsageSnapshot field for parity
    # with cache-write-aware providers, not an OpenAI undercount.
    return UsageSnapshot(
        input_tokens=int(payload.get("prompt_tokens", 0) or 0),
        output_tokens=int(payload.get("completion_tokens", 0) or 0),
        cache_read_tokens=int(
            (payload.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            or 0
        ),
    )


# --- no-op dispatcher -------------------------------------------------------


@dataclass
class NoOpDispatcher:
    """``ToolDispatcher`` that rejects every call.

    Use for REPL sessions that don't register tools yet. If the model
    nonetheless emits a ``ToolUseBlock``, dispatch raises so the failure
    is loud rather than silently returning an "is_error" string.
    """

    async def dispatch(
        self, name: str, input: dict[str, Any]
    ) -> tuple[str, bool]:
        raise RuntimeError(
            f"NoOpDispatcher refuses to dispatch {name!r}: no tools are registered"
        )


# --- httpx-backed production stream factory --------------------------------


def httpx_chat_completion_stream(
    *,
    api_key: str,
    base_url: str,
    extra_params: dict[str, Any] | None = None,
    timeout_seconds: float = 60.0,
) -> StreamChatCompletion:
    """Build a production ``StreamChatCompletion`` that talks SSE via httpx.

    ``base_url`` should be the OpenAI-compatible v1 prefix, e.g.
    ``http://127.0.0.1:4000/v1`` for LiteLLM or
    ``https://<resource>.cognitiveservices.azure.com/openai/v1`` for Azure.
    Trailing slashes are tolerated.
    """
    import httpx

    base = base_url.rstrip("/")
    url = f"{base}/chat/completions"
    extras = dict(extra_params or {})

    async def _stream(
        messages: Sequence[dict[str, Any]], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            **extras,
        }
        # Reasoning models (gpt-5/o1/o3/o4) reject ``max_tokens`` — translate it
        # to ``max_completion_tokens`` via the shared wire helper, so this engine
        # path doesn't 400 where the api/openai substrate already handles it.
        body = apply_token_limit(body, model)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async def _iter() -> AsyncIterator[dict[str, Any]]:
            async with (
                httpx.AsyncClient(timeout=timeout_seconds) as client,
                client.stream("POST", url, json=body, headers=headers) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue

        return _iter()

    return _stream


__all__ = [
    "NoOpDispatcher",
    "OpenAIChatStreamer",
    "StreamChatCompletion",
    "conversation_to_openai_messages",
    "httpx_chat_completion_stream",
]
