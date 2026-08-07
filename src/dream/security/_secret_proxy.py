"""Opaque secret placeholders resolved only at trusted tool execution."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeGuard

from dream.api.structured import JsonValue

_PLACEHOLDER_PREFIX = "dream_secret_"


def _is_json_value(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


@dataclass(frozen=True)
class SecretName:
    """Non-empty logical name for a registered secret."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            msg = "SecretName must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True)
class SecretPlaceholder:
    """Opaque placeholder string exposed to the model and transcript."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.startswith(_PLACEHOLDER_PREFIX):
            msg = f"SecretPlaceholder must start with {_PLACEHOLDER_PREFIX!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class SecretBinding:
    """Registered secret metadata without exposing the raw value."""

    name: SecretName
    placeholder: SecretPlaceholder


def _transform_json_value(value: JsonValue, transform: Callable[[str], str]) -> JsonValue:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [_transform_json_value(item, transform) for item in value]
    if isinstance(value, Mapping):
        return {key: _transform_json_value(item, transform) for key, item in value.items()}
    return value


class SecretProxy:
    """Hold secret values privately; expose stable placeholders to the model."""

    def __init__(self, *, token_factory: Callable[[], str] | None = None) -> None:
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(8))
        self._values: dict[SecretName, str] = {}
        self._placeholders: dict[SecretName, SecretPlaceholder] = {}

    def register(self, name: str, value: str) -> str:
        """Register a secret and return its stable placeholder string."""
        if not name:
            msg = "secret name must be non-empty"
            raise ValueError(msg)
        if not value:
            msg = "secret value must be non-empty"
            raise ValueError(msg)
        secret_name = SecretName(name)
        existing = self._placeholders.get(secret_name)
        if existing is not None:
            return existing.value
        token = self._token_factory()
        placeholder = SecretPlaceholder(f"{_PLACEHOLDER_PREFIX}{token}")
        self._values[secret_name] = value
        self._placeholders[secret_name] = placeholder
        return placeholder.value

    def placeholder_for(self, name: str) -> str:
        """Return the placeholder for a registered secret name."""
        secret_name = SecretName(name)
        placeholder = self._placeholders.get(secret_name)
        if placeholder is None:
            msg = f"unknown secret name: {name!r}"
            raise KeyError(msg)
        return placeholder.value

    def binding_for(self, name: str) -> SecretBinding:
        """Return name + placeholder metadata without the secret value."""
        secret_name = SecretName(name)
        placeholder = self._placeholders.get(secret_name)
        if placeholder is None:
            msg = f"unknown secret name: {name!r}"
            raise KeyError(msg)
        return SecretBinding(name=secret_name, placeholder=placeholder)

    def resolve_in_text(self, text: str) -> str:
        """Replace known placeholders with their secret values."""
        resolved = text
        for secret_name, placeholder in self._placeholders.items():
            secret_value = self._values[secret_name]
            resolved = resolved.replace(placeholder.value, secret_value)
        return resolved

    def resolve_in_json(self, data: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        """Deep-resolve placeholders inside a tool argument map."""
        return {
            key: _transform_json_value(value, self.resolve_in_text)
            for key, value in data.items()
        }

    def resolve_tool_input(self, data: Mapping[str, object]) -> dict[str, JsonValue]:
        """Validate a tool-arg map as JSON values, then resolve placeholders."""
        typed: dict[str, JsonValue] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                msg = f"tool input key must be str, got {type(key).__name__}"
                raise TypeError(msg)
            if not _is_json_value(value):
                msg = f"tool input value for {key!r} is not JSON-serializable"
                raise TypeError(msg)
            typed[key] = value
        return self.resolve_in_json(typed)

    def redact_text(self, text: str) -> str:
        """Replace raw secret values with their placeholders (longest first)."""
        if not self._values:
            return text
        replacements = sorted(
            (
                (self._values[secret_name], placeholder.value)
                for secret_name, placeholder in self._placeholders.items()
            ),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        redacted = text
        for secret_value, placeholder in replacements:
            redacted = redacted.replace(secret_value, placeholder)
        return redacted

    def redact_json(self, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Deep-redact secret values inside a JSON-shaped map."""
        return {
            key: _transform_json_value(value, self.redact_text) for key, value in data.items()
        }


__all__ = [
    "SecretBinding",
    "SecretName",
    "SecretPlaceholder",
    "SecretProxy",
]
