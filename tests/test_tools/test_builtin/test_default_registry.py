"""Default tool registry composition pin."""

from __future__ import annotations

import pytest

from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry


def test_default_registry_holds_all_six_default_tools() -> None:
    reg = default_registry()
    names = [t.name for t in reg.list_tools()]
    assert set(names) == {
        "read_file",
        "edit_file",
        "write_file",
        "bash",
        "git",
        "read_offloaded",
    }


def test_default_registry_order_is_canonical() -> None:
    """Order is byte-stable so the model-facing API schema does not jitter."""
    reg = default_registry()
    names = [t.name for t in reg.list_tools()]
    assert names == [
        "read_file",
        "edit_file",
        "write_file",
        "bash",
        "git",
        "read_offloaded",
    ]


def test_default_registry_tools_are_marked_default_source() -> None:
    from dream.tools._registry import ToolCollisionError

    reg = default_registry()
    # Re-registering an already-present tool collides regardless of source.
    tool = next(iter(reg))
    with pytest.raises(ToolCollisionError):
        reg.register(tool, source=ToolSource.PER_REPO)


def test_default_registry_is_independent_between_calls() -> None:
    a = default_registry()
    b = default_registry()
    assert a is not b
    assert [t.name for t in a] == [t.name for t in b]
