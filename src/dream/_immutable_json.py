"""Immutable value objects for JSON captured by durable read models."""

from __future__ import annotations

from collections import UserDict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TypeAlias

from dream.api.structured import JsonPrimitive, JsonValue


@dataclass(frozen=True)
class FrozenJsonArray:
    values: tuple[FrozenJsonValue, ...]


@dataclass(frozen=True)
class FrozenJsonObject(Mapping[str, object]):
    entries: tuple[tuple[str, FrozenJsonValue], ...] = ()

    @classmethod
    def capture(cls, raw: Mapping[str, object]) -> FrozenJsonObject:
        return cls(tuple((key, _freeze(value, key=key)) for key, value in raw.items()))

    def __getitem__(self, key: str) -> FrozenJsonValue:
        for candidate, value in self.entries:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def thaw(self) -> Mapping[str, JsonValue]:
        thawed: UserDict[str, JsonValue] = UserDict()
        for key, value in self.entries:
            thawed[key] = _thaw(value)
        return thawed


FrozenJsonValue: TypeAlias = JsonPrimitive | FrozenJsonArray | FrozenJsonObject


def _freeze(value: object, *, key: str) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return FrozenJsonArray(tuple(_freeze(item, key=key) for item in value))
    if isinstance(value, Mapping):
        if not all(isinstance(candidate, str) for candidate in value):
            raise ValueError(f"JSON object value for {key!r} has a non-string key")
        return FrozenJsonObject.capture(value)
    raise ValueError(f"value for {key!r} is not JSON-compatible")


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, FrozenJsonArray):
        return [_thaw(item) for item in value.values]
    if isinstance(value, FrozenJsonObject):
        return value.thaw()
    return value


__all__ = ["FrozenJsonArray", "FrozenJsonObject", "FrozenJsonValue"]
