"""Spec 04 Wave B — overflow error classification tests."""

from __future__ import annotations

import httpx

from dream.errors import ProviderError
from dream.services.compact._overflow import is_context_length_overflow


def _http_error(
    status: int,
    *,
    json_body: dict | None = None,
    text: str | None = None,
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    if json_body is not None:
        response = httpx.Response(status, request=request, json=json_body)
    else:
        response = httpx.Response(status, request=request, text=text or "")
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def test_overflow_prefers_structured_error_code() -> None:
    exc = _http_error(
        400,
        json_body={"error": {"code": "context_length_exceeded", "message": "whatever"}},
    )
    assert is_context_length_overflow(exc)


def test_overflow_413_without_body_hint() -> None:
    assert is_context_length_overflow(_http_error(413, text="payload too large"))


def test_overflow_openai_style_message_fallback() -> None:
    exc = _http_error(
        400,
        json_body={
            "error": {
                "message": "This model's maximum context length is 128000 tokens",
            }
        },
    )
    assert is_context_length_overflow(exc)


def test_bare_400_without_overflow_signal_is_not_overflow() -> None:
    """A malformed-request 400 must not trigger PTL recovery."""
    exc = _http_error(
        400,
        json_body={"error": {"code": "invalid_request_error", "message": "missing model"}},
    )
    assert not is_context_length_overflow(exc)


def test_structured_non_overflow_code_vetoes_prose_fallback() -> None:
    """Unrelated errors that mention token limits must not classify as PTL."""
    exc = _http_error(
        500,
        json_body={
            "error": {
                "code": "internal_server_error",
                "message": "reduce the size of your request to stay under the token limit",
            }
        },
    )
    assert not is_context_length_overflow(exc)


def test_auth_401_is_not_overflow() -> None:
    exc = _http_error(401, json_body={"error": {"message": "invalid key"}})
    assert not is_context_length_overflow(exc)


def test_provider_error_with_stamped_code() -> None:
    assert is_context_length_overflow(
        ProviderError("too big", code="dream.provider.context_length_exceeded")
    )
