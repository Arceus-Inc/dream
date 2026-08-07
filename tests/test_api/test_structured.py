"""Tests for ``resolve_structured_output`` + ``complete_structured``."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import MagicMock, patch

import pytest

from dream.api.response_format import (
    JsonSchema,
    ResponseFormat,
    ResponseFormatKind,
    resolve_structured_output,
)
from dream.api.structured import ChatMessage, ChatRole, StructuredResult, complete_structured

_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def test_resolve_structured_output_json_schema() -> None:
    out = resolve_structured_output(schema=_SCHEMA, name="verdict", strict=True)
    assert out is not None
    assert out.kind is ResponseFormatKind.JSON_SCHEMA
    assert out.json_schema is not None
    assert out.json_schema.name == "verdict"
    assert out.json_schema.strict is True
    assert dict(out.json_schema.schema.document) == dict(_SCHEMA)
    assert out.to_openai() == {
        "type": "json_schema",
        "json_schema": {
            "name": "verdict",
            "schema": dict(_SCHEMA),
            "strict": True,
        },
    }


def test_resolve_structured_output_json_mode() -> None:
    out = resolve_structured_output(json_mode=True)
    assert out == ResponseFormat.json_object()
    assert out.to_openai() == {"type": "json_object"}


def test_resolve_structured_output_empty_when_unset() -> None:
    assert resolve_structured_output() is None


def test_complete_structured_requires_schema_or_json_mode() -> None:
    with pytest.raises(ValueError, match="schema"):
        complete_structured(
            api_key="k",
            base_url="http://example.test/v1",
            model="gpt-4o",
            messages=[ChatMessage(role=ChatRole.USER, content="hi")],
        )


def test_complete_structured_posts_response_format() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": '{"answer": "ok"}'}}]}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        result = complete_structured(
            api_key="sk-test",
            base_url="http://example.test/v1",
            model="gpt-4o",
            messages=[ChatMessage(role=ChatRole.USER, content="hi")],
            schema=JsonSchema.of(_SCHEMA),
            schema_name="answer",
        )

    assert isinstance(result, StructuredResult)
    assert result.parsed == {"answer": "ok"}
    assert result.text == '{"answer": "ok"}'
    _args, kwargs = mock_client.post.call_args
    body = kwargs["json"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "answer"
    assert body["stream"] is False
