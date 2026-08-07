"""Live e2e: ``complete_structured`` against the configured Azure OpenAI deployment.

Loads credentials from the sibling chorus ``.env`` (same pattern as keyed smokes).
Skips cleanly when keys are unset.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dream.api.response_format import JsonSchema
from dream.api.structured import ChatMessage, ChatRole, StructuredContentType, complete_structured

_SCHEMA = JsonSchema.of(
    {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer", "confidence"],
        "additionalProperties": False,
    }
)


def _load_azure_env() -> tuple[str, str, str] | None:
    chorus_env = Path(__file__).resolve().parents[3] / "chorus" / ".env"
    if chorus_env.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(chorus_env, override=True)
        except ImportError:
            pass
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    base_url = os.environ.get("AZURE_OPENAI_BASE_URL")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if not (api_key and base_url and deployment):
        return None
    return api_key, base_url, deployment


def test_live_complete_structured_json_schema() -> None:
    creds = _load_azure_env()
    if creds is None:
        pytest.skip("AZURE_OPENAI_API_KEY / BASE_URL / DEPLOYMENT unset")
    api_key, base_url, deployment = creds

    result = complete_structured(
        api_key=api_key,
        base_url=base_url,
        model=deployment,
        messages=[
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    'Return JSON only: {"answer": "pong", "confidence": 0.9}. No prose, no fences.'
                ),
            )
        ],
        schema=_SCHEMA,
        schema_name="ping",
        strict=True,
        timeout_seconds=90.0,
    )

    assert result.content_type is StructuredContentType.JSON
    assert isinstance(result.parsed, dict)
    assert result.parsed.get("answer") == "pong"
    assert isinstance(result.parsed.get("confidence"), (int, float))
