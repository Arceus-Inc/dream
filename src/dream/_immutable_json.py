"""Immutable value objects for JSON captured by durable read models."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias, overload

from dream.api.structured import JsonPrimitive, JsonValue


@dataclass(frozen=True)
class FrozenJsonArray(Sequence["FrozenJsonValue"]):
    """Tuple-backed JSON array; nested values are sealed at construction."""

    values: tuple[FrozenJsonValue, ...] = ()

    def __post_init__(self) -> None:
        sealed = tuple(freeze_json_value(item, key="$") for item in self.values)
        object.__setattr__(self, "values", sealed)

    @classmethod
    def capture(cls, raw: Sequence[object]) -> FrozenJsonArray:
        """Deep-freeze ``raw``. Already-frozen arrays are returned as-is."""
        if isinstance(raw, FrozenJsonArray):
            return raw
        if isinstance(raw, (str, bytes)):
            raise ValueError("JSON array cannot be captured from a string")
        return cls(tuple(freeze_json_value(item) for item in raw))

    @overload
    def __getitem__(self, index: int) -> FrozenJsonValue: ...

    @overload
    def __getitem__(self, index: slice) -> FrozenJsonArray: ...

    def __getitem__(self, index: int | slice) -> FrozenJsonValue | FrozenJsonArray:
        if isinstance(index, slice):
            return FrozenJsonArray(self.values[index])
        return self.values[index]

    def __len__(self) -> int:
        return len(self.values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (str, bytes, Mapping)):
            return NotImplemented
        if not isinstance(other, Sequence):
            return NotImplemented
        if len(self) != len(other):
            return False
        return all(left == right for left, right in zip(self, other, strict=True))

    def __hash__(self) -> int:
        return hash(self.values)

    def thaw(self) -> list[JsonValue]:
        """Return a deep copy as a plain JSON array."""
        return [thaw_json_value(item) for item in self.values]


@dataclass(frozen=True)
class FrozenJsonObject(Mapping[str, "FrozenJsonValue"]):
    """Tuple-backed JSON object; nested values are sealed at construction.

    Implements ``Mapping`` so callers can read with ``obj[key]`` without
    exposing a mutable dict as the domain model. Equality is key-set based
    and ignores entry order.
    """

    entries: tuple[tuple[str, FrozenJsonValue], ...] = ()

    def __post_init__(self) -> None:
        sealed: list[tuple[str, FrozenJsonValue]] = []
        seen: dict[str, int] = {}
        for key, value in self.entries:
            if not isinstance(key, str):
                raise ValueError(f"JSON object key must be str, got {type(key).__name__}")
            frozen = freeze_json_value(value, key=key)
            existing = seen.get(key)
            if existing is None:
                seen[key] = len(sealed)
                sealed.append((key, frozen))
            else:
                sealed[existing] = (key, frozen)
        object.__setattr__(self, "entries", tuple(sealed))

    @classmethod
    def capture(cls, raw: Mapping[str, object]) -> FrozenJsonObject:
        """Deep-freeze ``raw``. Already-frozen objects are returned as-is."""
        if isinstance(raw, FrozenJsonObject):
            return raw
        entries: list[tuple[str, FrozenJsonValue]] = []
        for key, value in raw.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON object key must be str, got {type(key).__name__}")
            entries.append((key, freeze_json_value(value, key=key)))
        return cls(tuple(entries))

    def __getitem__(self, key: str) -> FrozenJsonValue:
        for candidate, value in self.entries:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        if len(self) != len(other):
            return False
        for key, value in self.items():
            try:
                if value != other[key]:
                    return False
            except (KeyError, TypeError):
                return False
        return True

    def __hash__(self) -> int:
        return hash(frozenset(self.entries))

    def thaw(self) -> dict[str, JsonValue]:
        """Return a deep copy as plain JSON containers (lists and dicts)."""
        return {key: thaw_json_value(value) for key, value in self.entries}


FrozenJsonValue: TypeAlias = JsonPrimitive | FrozenJsonArray | FrozenJsonObject


def freeze_json_value(value: object, *, key: str = "$") -> FrozenJsonValue:
    """Deep-freeze one JSON value, copying nested lists and mappings."""
    if isinstance(value, FrozenJsonArray):
        return value
    if isinstance(value, FrozenJsonObject):
        return value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return FrozenJsonArray(tuple(value))
    if isinstance(value, Mapping):
        return FrozenJsonObject.capture(value)
    raise ValueError(f"value for {key!r} is not JSON-compatible")


def thaw_json_value(value: FrozenJsonValue) -> JsonValue:
    """Deep-copy a frozen JSON value into lists and dicts."""
    if isinstance(value, FrozenJsonArray):
        return value.thaw()
    if isinstance(value, FrozenJsonObject):
        return value.thaw()
    return value


__all__ = [
    "FrozenJsonArray",
    "FrozenJsonObject",
    "FrozenJsonValue",
    "freeze_json_value",
    "thaw_json_value",
]
