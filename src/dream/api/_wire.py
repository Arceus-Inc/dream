"""Shared OpenAI-compatible wire helpers.

The reasoning-model token-limit quirk (``gpt-5``/``o1``/``o3``/``o4`` reject
``max_tokens`` and require ``max_completion_tokens``) is needed by *both*
OpenAI-compatible adapters:

- ``dream.api.openai`` — the Spec-02 single-prompt substrate.
- ``dream.engine._adapter_openai`` — the Spec-03 streaming ``TurnStreamer``.

Keeping it here means there is exactly one place to update when a new
reasoning-model family lands, instead of two adapters drifting apart.

Structured-output types live in :mod:`dream.api.response_format`;
``resolve_structured_output`` is re-exported here for a single import path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dream.api.response_format import (
    JsonSchema,
    JsonSchemaBinding,
    ResponseFormat,
    ResponseFormatKind,
    resolve_structured_output,
)

_REASONING_MODEL_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    """Whether ``model`` is a reasoning model that rejects ``max_tokens``.

    Tolerates a provider/route prefix (``openai/o3`` -> ``o3``) so Azure
    deployment routes and gateway-prefixed names classify correctly.
    """
    normalized = model.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized.startswith(_REASONING_MODEL_PREFIXES)


def token_limit_param(model: str, max_tokens: int) -> Mapping[str, int]:
    """Return the correct token-limit kwarg for ``model``.

    ``{"max_completion_tokens": n}`` for reasoning models, else
    ``{"max_tokens": n}``.
    """
    if is_reasoning_model(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def apply_token_limit(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Return ``body`` with a ``max_tokens`` key translated for reasoning models.

    A no-op (and returns the same object) when ``body`` has no ``max_tokens`` or
    ``model`` is not a reasoning model; otherwise returns a shallow copy with
    ``max_tokens`` renamed to ``max_completion_tokens`` so the request isn't
    rejected with a 400.
    """
    if "max_tokens" not in body or not is_reasoning_model(model):
        return body
    out = dict(body)
    out["max_completion_tokens"] = out.pop("max_tokens")
    return out


__all__ = [
    "JsonSchema",
    "JsonSchemaBinding",
    "ResponseFormat",
    "ResponseFormatKind",
    "apply_token_limit",
    "is_reasoning_model",
    "resolve_structured_output",
    "token_limit_param",
]
