"""Typed OTel attribute values — no Any, no open dict bags."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

AttributePrimitive = str | bool | int | float
AttributeValue = AttributePrimitive | Sequence[AttributePrimitive]
AttributeMap = Mapping[str, AttributeValue]
MutableAttributeMap = dict[str, AttributeValue]


def coerce_attributes(attributes: AttributeMap | None) -> MutableAttributeMap:
    """Copy attributes into a mutable map (omit None container)."""
    if attributes is None:
        return {}
    return {key: value for key, value in attributes.items()}


def merge_attributes(
    base: AttributeMap,
    extra: AttributeMap,
) -> MutableAttributeMap:
    merged = coerce_attributes(base)
    merged.update(coerce_attributes(extra))
    return merged


def filter_attribute_map(attributes: Mapping[str, object] | None) -> MutableAttributeMap:
    """Keep only OTel-legal attribute values; stringify the rest."""
    if attributes is None:
        return {}
    out: MutableAttributeMap = {}
    for key, value in attributes.items():
        if isinstance(value, (str, bool, int, float)):
            out[key] = value
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            items = list(value)
            if all(isinstance(item, (str, bool, int, float)) for item in items):
                out[key] = tuple(items)
            else:
                out[key] = tuple(str(item) for item in items)
        else:
            out[key] = str(value)
    return out


__all__ = [
    "AttributeMap",
    "AttributePrimitive",
    "AttributeValue",
    "MutableAttributeMap",
    "coerce_attributes",
    "filter_attribute_map",
    "merge_attributes",
]
