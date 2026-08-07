"""Unit tests for ``dream.security.SecretProxy``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from dream.api.structured import JsonValue
from dream.security import SecretName, SecretPlaceholder, SecretProxy


def _deterministic_tokens() -> list[str]:
    return ["tok001", "tok002", "tok003"]


def _token_iter() -> Iterator[str]:
    return iter(_deterministic_tokens())


def test_register_returns_dream_secret_placeholder() -> None:
    proxy = SecretProxy(token_factory=lambda: next(_token_iter()))
    placeholder = proxy.register("api_key", "sk-live-secret")
    assert placeholder.startswith("dream_secret_")
    assert placeholder == "dream_secret_tok001"
    assert proxy.placeholder_for("api_key") == placeholder


def test_register_rejects_empty_name_or_value() -> None:
    proxy = SecretProxy(token_factory=lambda: "tok")
    with pytest.raises(ValueError, match="name"):
        proxy.register("", "value")
    with pytest.raises(ValueError, match="value"):
        proxy.register("name", "")


def test_placeholder_for_unknown_name_raises() -> None:
    proxy = SecretProxy(token_factory=lambda: "tok")
    with pytest.raises(KeyError):
        proxy.placeholder_for("missing")


def test_resolve_in_text_roundtrip() -> None:
    proxy = SecretProxy(token_factory=lambda: "abc123")
    placeholder = proxy.register("token", "super-secret-value")
    resolved = proxy.resolve_in_text(f"Bearer {placeholder} end")
    assert resolved == "Bearer super-secret-value end"
    redacted = proxy.redact_text(resolved)
    assert redacted == f"Bearer {placeholder} end"


def test_resolve_in_json_and_redact_json_nested() -> None:
    proxy = SecretProxy(token_factory=lambda: "nested")
    placeholder = proxy.register("db_pass", "p@ssw0rd!")
    data: dict[str, JsonValue] = {
        "config": {
            "password": placeholder,
            "hosts": [placeholder, "localhost"],
        },
        "note": f"uses {placeholder}",
    }
    resolved = proxy.resolve_in_json(data)
    assert resolved == {
        "config": {
            "password": "p@ssw0rd!",
            "hosts": ["p@ssw0rd!", "localhost"],
        },
        "note": "uses p@ssw0rd!",
    }
    redacted = proxy.redact_json(resolved)
    assert redacted == data


def test_redact_longest_secret_first() -> None:
    proxy = SecretProxy(token_factory=lambda: next(_token_iter()))
    short_ph = proxy.register("short", "abc")
    long_ph = proxy.register("long", "abcd")
    raw = "prefix abcd suffix abc end"
    redacted = proxy.redact_text(raw)
    assert redacted == f"prefix {long_ph} suffix {short_ph} end"


def test_register_is_stable_per_name() -> None:
    proxy = SecretProxy(token_factory=lambda: next(_token_iter()))
    first = proxy.register("key", "v1")
    second = proxy.register("key", "v1")
    assert first == second


def test_secret_name_rejects_empty() -> None:
    with pytest.raises(ValueError):
        SecretName("")


def test_secret_placeholder_prefix() -> None:
    ph = SecretPlaceholder("dream_secret_xyz")
    assert ph.value == "dream_secret_xyz"
