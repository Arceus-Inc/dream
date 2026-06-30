"""Unit tests for dream.api.openai — OpenAI-compatible substrate adapter.

Covers constructor validation, complete(), stream(), count_tokens(),
max_window(), health(), timeout translation, and health classification.
All OpenAI SDK interactions are mocked so no network calls occur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dream.api._timeout import SubstrateTimeout
from dream.api.openai import OpenAIChatSubstrate, _approx_token_count
from dream.api.substrate import CompletionResult, HealthReport

# --- _approx_token_count (lines 44-46 already covered, but good baseline) ---


def test_approx_token_count_empty() -> None:
    assert _approx_token_count("") == 0


def test_approx_token_count_short() -> None:
    assert _approx_token_count("hi") == 1


def test_approx_token_count_normal() -> None:
    assert _approx_token_count("a" * 100) == 25


# --- Constructor validation (lines 70-80) ---


def test_constructor_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="non-empty api_key"):
        OpenAIChatSubstrate(name="test", api_key="", model="gpt-4o")


def test_constructor_rejects_empty_model() -> None:
    with pytest.raises(ValueError, match="non-empty model"):
        OpenAIChatSubstrate(name="test", api_key="sk-test", model="")


# --- Helpers ---


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20


@dataclass
class _FakeMessage:
    content: str | None = "hello world"


@dataclass
class _FakeChoice:
    message: _FakeMessage = field(default_factory=_FakeMessage)
    finish_reason: str = "stop"


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice] = field(default_factory=lambda: [_FakeChoice()])
    usage: _FakeUsage = field(default_factory=_FakeUsage)
    id: str = "cmpl-test"
    model: str = "gpt-4o"


@dataclass
class _FakeDelta:
    content: str | None = None


@dataclass
class _FakeStreamChoice:
    delta: _FakeDelta = field(default_factory=_FakeDelta)


@dataclass
class _FakeStreamChunk:
    choices: list[_FakeStreamChoice] = field(default_factory=list)


def _make_substrate(**kwargs: Any) -> OpenAIChatSubstrate:
    """Build a substrate with a mocked OpenAI client."""
    with patch.object(OpenAIChatSubstrate, "_build_client", return_value=MagicMock()):
        defaults: dict[str, Any] = {"name": "test", "api_key": "sk-test", "model": "gpt-4o"}
        defaults.update(kwargs)
        return OpenAIChatSubstrate(**defaults)


def _mock_client(sub: OpenAIChatSubstrate) -> MagicMock:
    """Return the MagicMock client so mypy sees MagicMock, not OpenAI."""
    client: MagicMock = sub._client  # type: ignore[assignment]
    return client


# --- complete() (lines 97-125) ---


def test_complete_returns_completion_result() -> None:
    sub = _make_substrate()
    _mock_client(sub).chat.completions.create.return_value = _FakeResponse()
    result = sub.complete("say hi")
    assert isinstance(result, CompletionResult)
    assert result.text == "hello world"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.finish_reason == "stop"
    assert result.raw["id"] == "cmpl-test"


def test_complete_uses_default_params() -> None:
    sub = _make_substrate(default_params={"temperature": 0.5})
    mock = _mock_client(sub)
    mock.chat.completions.create.return_value = _FakeResponse()
    sub.complete("say hi")
    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["temperature"] == 0.5


def test_complete_overrides_merge_with_defaults() -> None:
    sub = _make_substrate(default_params={"temperature": 0.5})
    mock = _mock_client(sub)
    mock.chat.completions.create.return_value = _FakeResponse()
    sub.complete("say hi", params={"temperature": 0.9})
    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["temperature"] == 0.9


def test_complete_no_choices_raises() -> None:
    sub = _make_substrate()
    _mock_client(sub).chat.completions.create.return_value = _FakeResponse(choices=[])
    with pytest.raises(RuntimeError, match="no choices"):
        sub.complete("say hi")


def test_complete_none_message_content_returns_empty() -> None:
    sub = _make_substrate()
    choice = _FakeChoice(message=_FakeMessage(content=None))
    _mock_client(sub).chat.completions.create.return_value = _FakeResponse(choices=[choice])
    result = sub.complete("say hi")
    assert result.text == ""


def test_complete_none_message_returns_empty() -> None:
    sub = _make_substrate()
    choice = _FakeChoice(message=None)  # type: ignore[arg-type]
    _mock_client(sub).chat.completions.create.return_value = _FakeResponse(choices=[choice])
    result = sub.complete("say hi")
    assert result.text == ""


def test_complete_none_usage_returns_zero_tokens() -> None:
    sub = _make_substrate()
    _mock_client(sub).chat.completions.create.return_value = _FakeResponse(usage=None)  # type: ignore[arg-type]
    result = sub.complete("say hi")
    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_complete_model_override_via_params() -> None:
    sub = _make_substrate()
    mock = _mock_client(sub)
    mock.chat.completions.create.return_value = _FakeResponse()
    sub.complete("say hi", params={"model": "gpt-4o-mini"})
    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "gpt-4o-mini"


def test_complete_max_tokens_param() -> None:
    sub = _make_substrate()
    mock = _mock_client(sub)
    mock.chat.completions.create.return_value = _FakeResponse()
    sub.complete("say hi", params={"max_tokens": 2048})
    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs.get("max_tokens") == 2048 or call_kwargs.get("max_completion_tokens") == 2048


# --- stream() (lines 127-150) ---


def test_stream_yields_content() -> None:
    sub = _make_substrate()
    chunks = [
        _FakeStreamChunk(choices=[_FakeStreamChoice(delta=_FakeDelta(content="hel"))]),
        _FakeStreamChunk(choices=[_FakeStreamChoice(delta=_FakeDelta(content="lo"))]),
        _FakeStreamChunk(choices=[]),  # empty choices chunk
        _FakeStreamChunk(choices=[_FakeStreamChoice(delta=_FakeDelta(content=None))]),
    ]
    _mock_client(sub).chat.completions.create.return_value = iter(chunks)
    pieces = list(sub.stream("say hi"))
    assert pieces == ["hel", "lo"]


# --- count_tokens() and max_window() (lines 152-156) ---


def test_count_tokens_delegates_to_approx() -> None:
    sub = _make_substrate()
    assert sub.count_tokens("hello world!") == _approx_token_count("hello world!")


def test_max_window_returns_configured_value() -> None:
    sub = _make_substrate(max_window_tokens=64_000)
    assert sub.max_window() == 64_000


# --- health() (lines 158-173) ---


def test_health_ok_when_models_list_succeeds() -> None:
    sub = _make_substrate()
    _mock_client(sub).models.list.return_value = []
    report = sub.health()
    assert isinstance(report, HealthReport)
    assert report.state == "ok"
    assert report.latency_ms >= 0


def test_health_degraded_on_auth_error() -> None:
    sub = _make_substrate()
    _mock_client(sub).models.list.side_effect = type("AuthenticationError", (Exception,), {})()
    report = sub.health()
    assert report.state == "degraded"
    assert "AuthenticationError" in report.detail


def test_health_degraded_on_permission_error() -> None:
    sub = _make_substrate()
    _mock_client(sub).models.list.side_effect = type("PermissionDeniedError", (Exception,), {})()
    report = sub.health()
    assert report.state == "degraded"


def test_health_down_on_timeout() -> None:
    sub = _make_substrate()
    _mock_client(sub).models.list.side_effect = type("APITimeoutError", (Exception,), {})()
    report = sub.health()
    assert report.state == "down"


def test_health_down_on_connection_error() -> None:
    sub = _make_substrate()
    _mock_client(sub).models.list.side_effect = type("APIConnectionError", (Exception,), {})()
    report = sub.health()
    assert report.state == "down"


def test_health_degraded_on_unknown_error() -> None:
    sub = _make_substrate()
    _mock_client(sub).models.list.side_effect = ValueError("unexpected")
    report = sub.health()
    assert report.state == "degraded"


# --- _translating_timeouts / _reraise_timeout (lines 183-201) ---


def test_translating_timeouts_propagates_non_timeout() -> None:
    sub = _make_substrate()
    with pytest.raises(ValueError, match="boom"):
        with sub._translating_timeouts():
            raise ValueError("boom")


def test_translating_timeouts_converts_timeout_to_substrate_timeout() -> None:
    sub = _make_substrate()
    exc_cls = type("APITimeoutError", (Exception,), {})
    with pytest.raises(SubstrateTimeout):
        with sub._translating_timeouts():
            raise exc_cls("request timed out")


def test_translating_timeouts_passes_through_on_no_error() -> None:
    sub = _make_substrate()
    with sub._translating_timeouts():
        pass  # no error


def test_complete_timeout_translates_to_substrate_timeout() -> None:
    sub = _make_substrate()
    exc_cls = type("APITimeoutError", (Exception,), {})
    _mock_client(sub).chat.completions.create.side_effect = exc_cls("timed out")
    with pytest.raises(SubstrateTimeout):
        sub.complete("say hi")


def test_stream_timeout_translates_to_substrate_timeout() -> None:
    sub = _make_substrate()
    exc_cls = type("APITimeoutError", (Exception,), {})

    def _exploding_stream(*a: Any, **kw: Any) -> Any:
        raise exc_cls("timed out")

    _mock_client(sub).chat.completions.create.side_effect = _exploding_stream
    with pytest.raises(SubstrateTimeout):
        list(sub.stream("say hi"))


# --- _classify_health (lines 203-213) ---


def test_classify_health_authentication() -> None:
    exc = type("AuthenticationError", (Exception,), {})("bad key")
    assert OpenAIChatSubstrate._classify_health(exc) == "degraded"


def test_classify_health_permission() -> None:
    exc = type("PermissionDeniedError", (Exception,), {})("forbidden")
    assert OpenAIChatSubstrate._classify_health(exc) == "degraded"


def test_classify_health_timeout() -> None:
    exc = type("APITimeoutError", (Exception,), {})("timed out")
    assert OpenAIChatSubstrate._classify_health(exc) == "down"


def test_classify_health_connection() -> None:
    exc = type("APIConnectionError", (Exception,), {})("refused")
    assert OpenAIChatSubstrate._classify_health(exc) == "down"


def test_classify_health_unknown() -> None:
    exc = ValueError("unexpected")
    assert OpenAIChatSubstrate._classify_health(exc) == "degraded"


# --- _merged_params (lines 177-181) ---


def test_merged_params_no_overrides() -> None:
    sub = _make_substrate(default_params={"temperature": 0.7})
    assert sub._merged_params(None) == {"temperature": 0.7}


def test_merged_params_with_overrides() -> None:
    sub = _make_substrate(default_params={"temperature": 0.7})
    result = sub._merged_params({"temperature": 0.9, "top_p": 0.5})
    assert result == {"temperature": 0.9, "top_p": 0.5}


def test_merged_params_no_defaults() -> None:
    sub = _make_substrate()
    assert sub._merged_params(None) == {}


# --- Substrate Protocol conformance ---


def test_substrate_protocol_conformance() -> None:
    from dream.api.substrate import Substrate

    sub = _make_substrate()
    assert isinstance(sub, Substrate)
