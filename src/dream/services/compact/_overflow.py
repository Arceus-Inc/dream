"""Context-length / prompt-too-long error classification (Spec 04 reactive path).

Hermes contract: overflow is a **compaction** problem, not a substrate failover
problem — classify provider rejections here so the act-loop can shrink + retry
once via :func:`react_to_ptl`, never rotate providers.

Classification priority (structured → semantic HTTP → message needles):

1. Vendor ``error.code`` / ``error.type`` when present (stable contracts).
2. HTTP 413 Payload Too Large (transport-level size reject).
3. A small frozenset of message *needles* for gateways that only return prose.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

# OpenAI / Azure OpenAI / LiteLLM / Anthropic-adjacent stable codes.
_OVERFLOW_ERROR_CODES: frozenset[str] = frozenset(
    {
        "context_length_exceeded",
        "string_above_max_length",
        "request_too_large",
        "prompt_too_long",
        "input_too_long",
    }
)

# Last-resort message needles — lowercase substrings, not per-vendor regexes.
# Kept small on purpose: prefer structured codes above when providers emit them.
_OVERFLOW_NEEDLES: frozenset[str] = frozenset(
    {
        "context length",
        "context_length",
        "maximum context",
        "prompt too long",
        "prompt is too long",
        "too many tokens",
        "token limit",
        "request too large",
        "reduce the length",
        "reduce the size",
    }
)

# Statuses that *may* carry a context rejection (still require a code/needle,
# except 413 which is Payload Too Large by HTTP semantics).
_CANDIDATE_HTTP_STATUSES: frozenset[int] = frozenset({400, 413, 422})


def _walk_error_fields(payload: Any) -> tuple[str | None, str]:
    """Pull ``(code_or_type, concatenated_message_text)`` from a JSON body."""
    code: str | None = None
    texts: list[str] = []

    def _visit(node: Any) -> None:
        nonlocal code
        if isinstance(node, dict):
            for key in ("code", "type", "error_code"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    code = code or value.strip()
            for key in ("message", "detail", "msg"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    texts.append(value)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    _visit(value)
        elif isinstance(node, list):
            for item in node:
                _visit(item)

    _visit(payload)
    return code, "\n".join(texts)


def _extract_signals(exc: BaseException) -> tuple[str | None, str, int | None]:
    """Return ``(error_code, haystack_text, http_status)`` from ``exc``."""
    status: int | None = None
    code: str | None = None
    parts: list[str] = [str(exc)]

    # Dream ``ProviderError`` (and similar) may stamp a stable ``code``.
    stamped = getattr(exc, "code", None)
    if isinstance(stamped, str) and stamped.strip():
        # Strip the ``dream.provider.*`` prefix if present; keep the leaf.
        leaf = stamped.rsplit(".", 1)[-1]
        code = leaf

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            status = int(response.status_code)
        except Exception:
            status = None
        body = ""
        try:
            body = response.text or ""
        except Exception:
            body = ""
        if body:
            parts.append(body)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                body_code, body_text = _walk_error_fields(payload)
                if body_code:
                    code = code or body_code
                if body_text:
                    parts.append(body_text)

    return code, "\n".join(parts), status


def _code_is_overflow(code: str | None) -> bool:
    if not code:
        return False
    normalized = code.strip().lower().replace("-", "_")
    if normalized in _OVERFLOW_ERROR_CODES:
        return True
    # Soft match: vendor codes often embed the contract name.
    return "context_length" in normalized or normalized.endswith("too_long")


def _message_is_overflow(text: str) -> bool:
    lowered = text.lower()
    return any(needle in lowered for needle in _OVERFLOW_NEEDLES)


def is_context_length_overflow(exc: BaseException) -> bool:
    """Return True when ``exc`` is a context-window / PTL rejection.

    Prefers structured ``error.code`` over message scraping. Never treats a
    bare 400 as overflow — that would steal real client errors from the
    FailoverStreamer fail-closed path.
    """
    code, text, status = _extract_signals(exc)

    if _code_is_overflow(code):
        return True

    if status == 413:
        return True

    # Structured non-overflow codes veto prose fallback — unrelated errors that
    # mention "token limit" in the message must not trigger PTL recovery.
    if code is not None:
        return False

    if (
        isinstance(exc, httpx.HTTPStatusError)
        and status is not None
        and status not in _CANDIDATE_HTTP_STATUSES
    ):
        return False

    return _message_is_overflow(text)


__all__ = ["is_context_length_overflow"]
