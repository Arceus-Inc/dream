"""Thin non-streaming structured completion (Hermes ``complete_structured`` shape).

One HTTP call with ``response_format`` — not an agent loop. Heads and the
subagent output-guard repair path use this for typed completes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias, TypeGuard

from dream.api._wire import apply_token_limit
from dream.api.response_format import JsonSchema, ResponseFormat, resolve_structured_output

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | Mapping[str, "JsonValue"]


class ChatRole(StrEnum):
    """OpenAI chat message role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ChatMessage:
    """One chat turn for :func:`complete_structured`."""

    role: ChatRole
    content: str

    def to_openai(self) -> Mapping[str, str]:
        return {"role": self.role.value, "content": self.content}


class StructuredContentType(StrEnum):
    """Whether :attr:`StructuredResult.parsed` holds JSON."""

    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True)
class StructuredResult:
    """Outcome of :func:`complete_structured`."""

    text: str
    parsed: JsonValue | None
    content_type: StructuredContentType = StructuredContentType.TEXT


@dataclass(frozen=True)
class StructuredCompletionRequest:
    """Non-streaming chat completion constrained by ``response_format``."""

    model: str
    messages: tuple[ChatMessage, ...]
    response_format: ResponseFormat
    stream: bool = False

    def to_openai_body(self) -> MutableMapping[str, object]:
        return {
            "model": self.model,
            "messages": [message.to_openai() for message in self.messages],
            "stream": self.stream,
            "response_format": dict(self.response_format.to_openai()),
        }


def complete_structured(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: Sequence[ChatMessage],
    schema: JsonSchema | Mapping[str, object] | None = None,
    json_mode: bool = False,
    schema_name: str = "structured_output",
    strict: bool = False,
    timeout_seconds: float = 60.0,
) -> StructuredResult:
    """Run one chat completion constrained by ``response_format``.

    Raises ``ValueError`` when neither ``schema`` nor ``json_mode`` is set.
    ``parsed`` is set when the content is valid JSON; schema validation is left
    to the caller (``enforce_output_schema`` / heads).
    """
    response_format = resolve_structured_output(
        schema=schema,
        json_mode=json_mode,
        name=schema_name,
        strict=strict,
    )
    if response_format is None:
        raise ValueError("complete_structured requires schema=... or json_mode=True")
    if not api_key:
        raise ValueError("complete_structured requires a non-empty api_key")
    if not model:
        raise ValueError("complete_structured requires a non-empty model")

    import httpx

    request = StructuredCompletionRequest(
        model=model,
        messages=tuple(messages),
        response_format=response_format,
    )
    # HTTP body boundary: OpenAI adapters still take untyped JSON dicts.
    body = apply_token_limit(dict(request.to_openai_body()), model)
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, Mapping):
        raise RuntimeError("complete_structured returned a non-object payload")
    return _parse_structured_payload(payload)


def _parse_structured_payload(payload: Mapping[str, object]) -> StructuredResult:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("complete_structured returned no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise RuntimeError("complete_structured returned a malformed choice")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("complete_structured returned a malformed message")
    raw_content = message.get("content")
    text = str(raw_content or "").strip()
    parsed = _try_parse_json(text)
    content_type = StructuredContentType.JSON if parsed is not None else StructuredContentType.TEXT
    return StructuredResult(text=text, parsed=parsed, content_type=content_type)


def _try_parse_json(text: str) -> JsonValue | None:
    if not text:
        return None
    try:
        value: object = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if _is_json_value(value):
        return value
    return None


def _is_json_value(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


__all__ = [
    "ChatMessage",
    "ChatRole",
    "JsonPrimitive",
    "JsonSchema",
    "JsonValue",
    "ResponseFormat",
    "StructuredCompletionRequest",
    "StructuredContentType",
    "StructuredResult",
    "complete_structured",
    "resolve_structured_output",
]
