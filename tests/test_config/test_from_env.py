"""Unit tests for dream.config.from_env — auth source env-var mapping.

Covers auth_source_env_var_candidates(), resolve_auth_env_value(),
and default_auth_source_for_provider().
"""

from __future__ import annotations

import pytest

from dream.config.from_env import (
    PROFILE_ENV,
    auth_source_env_var_candidates,
    default_auth_source_for_provider,
    resolve_auth_env_value,
)

# --- auth_source_env_var_candidates (lines 42-44) ---


def test_known_auth_source_returns_candidates() -> None:
    candidates = auth_source_env_var_candidates("openai_api_key")
    assert candidates == ("DREAM_OPENAI_API_KEY", "OPENAI_API_KEY")


def test_anthropic_auth_source() -> None:
    candidates = auth_source_env_var_candidates("anthropic_api_key")
    assert candidates == ("DREAM_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def test_azure_openai_has_three_candidates() -> None:
    candidates = auth_source_env_var_candidates("azure_openai_api_key")
    assert len(candidates) == 3
    assert "OPENAI_API_KEY" in candidates  # last-resort fallback


def test_unknown_auth_source_returns_empty_tuple() -> None:
    assert auth_source_env_var_candidates("nonexistent_source") == ()


def test_copilot_oauth_source() -> None:
    candidates = auth_source_env_var_candidates("copilot_oauth")
    assert "DREAM_COPILOT_TOKEN" in candidates


# --- resolve_auth_env_value (lines 47-53) ---


def test_resolve_dream_prefixed_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DREAM_OPENAI_API_KEY", "dream-key")
    monkeypatch.setenv("OPENAI_API_KEY", "bare-key")
    result = resolve_auth_env_value("openai_api_key")
    assert result is not None
    env_var, value = result
    assert env_var == "DREAM_OPENAI_API_KEY"
    assert value == "dream-key"


def test_resolve_falls_back_to_bare_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DREAM_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "bare-key")
    result = resolve_auth_env_value("openai_api_key")
    assert result is not None
    env_var, value = result
    assert env_var == "OPENAI_API_KEY"
    assert value == "bare-key"


def test_resolve_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DREAM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = resolve_auth_env_value("openai_api_key")
    assert result is None


def test_resolve_skips_empty_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DREAM_OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = resolve_auth_env_value("openai_api_key")
    assert result is None


def test_resolve_unknown_source() -> None:
    result = resolve_auth_env_value("nonexistent_source")
    assert result is None


# --- default_auth_source_for_provider (lines 66-82) ---


def test_explicit_provider_mapping() -> None:
    assert default_auth_source_for_provider("copilot") == "copilot_oauth"
    assert default_auth_source_for_provider("azure_openai") == "azure_openai_api_key"
    assert default_auth_source_for_provider("anthropic") == "anthropic_api_key"
    assert default_auth_source_for_provider("openai") == "openai_api_key"


def test_named_provider_gets_own_key_not_openai() -> None:
    assert default_auth_source_for_provider("groq", "openai") == "groq_api_key"
    assert default_auth_source_for_provider("deepseek") == "deepseek_api_key"
    assert default_auth_source_for_provider("openrouter", "openai") == "openrouter_api_key"


def test_unknown_provider_with_openai_format() -> None:
    assert default_auth_source_for_provider("", "openai") == "openai_api_key"


def test_unknown_provider_unknown_format() -> None:
    assert default_auth_source_for_provider("", None) == "api_key"
    assert default_auth_source_for_provider("") == "api_key"


# --- PROFILE_ENV constant ---


def test_profile_env_is_expected_value() -> None:
    assert PROFILE_ENV == "DREAM_PROFILE"
