"""Config-boundary validation for ProviderProfile / Settings (spec 02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dream.config.from_file import ProviderProfile, Settings


def _profile(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        label="X",
        provider="openai",
        api_format="openai",
        auth_source="openai_api_key",
        default_model="gpt-5-mini",
    )
    base.update(overrides)
    return base


def test_provider_profile_accepts_http_and_https_base_url() -> None:
    for url in ("http://127.0.0.1:4000/v1", "https://api.example.com/v1"):
        prof = ProviderProfile(**_profile(base_url=url))  # type: ignore[arg-type]
        assert prof.base_url == url


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://host/x", "javascript:alert(1)"])
def test_provider_profile_rejects_non_http_base_url(bad: str) -> None:
    with pytest.raises(ValidationError, match="http"):
        ProviderProfile(**_profile(base_url=bad))  # type: ignore[arg-type]


def test_provider_profile_rejects_unknown_keys() -> None:
    """A typo'd field must fail loudly, not silently fall back to a default."""
    with pytest.raises(ValidationError):
        ProviderProfile(**_profile(defaul_model="oops"))  # type: ignore[arg-type]


def test_settings_rejects_non_http_base_url() -> None:
    with pytest.raises(ValidationError, match="http"):
        Settings(base_url="file:///etc/passwd")


def test_settings_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationError):
        Settings(activ_profile="claude-api")  # type: ignore[call-arg]
