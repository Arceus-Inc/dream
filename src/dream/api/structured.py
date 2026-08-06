"""Thin non-streaming structured completion (Hermes ``complete_structured`` shape).

One HTTP call with ``response_format`` — not an agent loop. Heads and the
subagent output-guard repair path use this for typed completes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dream.api._wire import apply_token_limit, resolve_structured_output


@dataclass(frozen=True)
class StructuredResult:
    """Outcome of :func:`complete_structured`."""

    text: str
    parsed: Any | None


def complete_structured(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: Sequence[dict[str, Any]],
    schema: dict[str, Any] | None = None,
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
    if schema is None and not json_mode:
        raise ValueError("complete_structured requires schema=... or json_mode=True")
    if not api_key:
        raise ValueError("complete_structured requires a non-empty api_key")
    if not model:
        raise ValueError("complete_structured requires a non-empty model")

    import httpx

    url = f"{base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": False,
        **resolve_structured_output(
            schema=schema,
            json_mode=json_mode,
            name=schema_name,
            strict=strict,
        ),
    }
    body = apply_token_limit(body, model)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("complete_structured returned no choices")
    message = choices[0].get("message") or {}
    text = str(message.get("content") or "").strip()
    parsed: Any | None
    try:
        parsed = json.loads(text) if text else None
    except (json.JSONDecodeError, ValueError):
        parsed = None
    return StructuredResult(text=text, parsed=parsed)


__all__ = ["StructuredResult", "complete_structured"]
