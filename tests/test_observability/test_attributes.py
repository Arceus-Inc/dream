"""Attribute filtering keeps OTel-legal primitives and stringifies the rest."""

from __future__ import annotations

from dream.observability._attributes import (
    coerce_attributes,
    filter_attribute_map,
    merge_attributes,
)


def test_filter_keeps_primitives_and_stringifies_other() -> None:
    filtered = filter_attribute_map(
        {
            "s": "ok",
            "b": True,
            "i": 3,
            "f": 1.5,
            "seq": ["a", 1],
            "bad_seq": [{"x": 1}],
            "other": object(),
        }
    )
    assert filtered["s"] == "ok"
    assert filtered["b"] is True
    assert filtered["i"] == 3
    assert filtered["f"] == 1.5
    assert filtered["seq"] == ("a", 1)
    assert filtered["bad_seq"] == (str({"x": 1}),)
    assert isinstance(filtered["other"], str)


def test_coerce_and_merge() -> None:
    base = coerce_attributes({"a": 1})
    merged = merge_attributes(base, {"b": "x"})
    assert merged == {"a": 1, "b": "x"}
