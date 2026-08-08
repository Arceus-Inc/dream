"""Spec 05 slice A — ``ToolRegistry`` ordering + collision rules.

Acceptance criteria:
- ``register`` records each tool by name with a source tag.
- ``list`` returns a **deterministic** order: built-in default canonical →
  per-repo alphabetical → skill/MCP alphabetical (spec §"Registry semantics").
- Re-registering a default tool with the same name from a per-repo source
  is rejected (collision); same source repeated is rejected (duplicate).
- ``to_api_schema()`` emits the schemas in the same deterministic order so
  every session sees the same tool list shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import (
    ToolCollisionError,
    ToolRegistry,
    ToolSource,
)


class _StubIn(BaseModel):
    pass


def _make_tool(name: str, *, risk: str = "safe") -> BaseTool:
    class _T(BaseTool):
        pass

    _T.name = name
    _T.description = f"stub {name}"
    _T.declaration = ToolDeclaration(
        risk=risk,  # type: ignore[arg-type]
        tier_required=0,
        timeout_seconds=5.0,
    )
    _T.input_model = _StubIn
    _T.__abstractmethods__ = frozenset()  # type: ignore[attr-defined]

    async def execute(
        self: BaseTool, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(content="")

    _T.execute = execute  # type: ignore[assignment, method-assign]
    return _T()


def test_register_and_get() -> None:
    reg = ToolRegistry()
    t = _make_tool("alpha")
    reg.register(t, source=ToolSource.DEFAULT)
    assert reg.get("alpha") is t
    assert reg.get("nope") is None


def test_list_returns_default_canonical_order_first() -> None:
    reg = ToolRegistry(default_order=("bash", "read_file", "write_file"))
    reg.register(_make_tool("write_file"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("read_file"), source=ToolSource.DEFAULT)
    names = [t.name for t in reg.list_tools()]
    assert names == ["bash", "read_file", "write_file"]


def test_list_orders_per_repo_alpha_after_defaults() -> None:
    reg = ToolRegistry(default_order=("bash",))
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("zeta_repo"), source=ToolSource.PER_REPO)
    reg.register(_make_tool("alpha_repo"), source=ToolSource.PER_REPO)
    names = [t.name for t in reg.list_tools()]
    assert names == ["bash", "alpha_repo", "zeta_repo"]


def test_list_orders_skill_and_mcp_alpha_after_per_repo() -> None:
    reg = ToolRegistry(default_order=("bash",))
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("repo_tool"), source=ToolSource.PER_REPO)
    reg.register(_make_tool("zmcp"), source=ToolSource.MCP)
    reg.register(_make_tool("askill"), source=ToolSource.SKILL)
    names = [t.name for t in reg.list_tools()]
    assert names == ["bash", "repo_tool", "askill", "zmcp"]


def test_list_is_stable_across_calls() -> None:
    reg = ToolRegistry(default_order=("bash", "edit"))
    reg.register(_make_tool("edit"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("x_repo"), source=ToolSource.PER_REPO)
    a = [t.name for t in reg.list_tools()]
    b = [t.name for t in reg.list_tools()]
    assert a == b


def test_duplicate_same_source_raises() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    try:
        reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    except ToolCollisionError as e:
        assert "bash" in str(e)
    else:
        raise AssertionError("expected ToolCollisionError on duplicate registration")


def test_collision_across_sources_raises() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    try:
        reg.register(_make_tool("bash"), source=ToolSource.PER_REPO)
    except ToolCollisionError as e:
        assert "bash" in str(e)
        assert "default" in str(e).lower() or "per" in str(e).lower()
    else:
        raise AssertionError("expected ToolCollisionError on cross-source collision")


def test_replace_only_allows_default_to_per_repo() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    prior = reg.register(_make_tool("bash"), source=ToolSource.PER_REPO, replace=True)
    assert prior is ToolSource.DEFAULT

    reg.register(_make_tool("repo"), source=ToolSource.PER_REPO)
    try:
        reg.register(_make_tool("repo"), source=ToolSource.PER_REPO, replace=True)
    except ToolCollisionError:
        pass
    else:
        raise AssertionError("expected duplicate per-repo registration to fail")


def test_to_api_schema_matches_list_order() -> None:
    reg = ToolRegistry(default_order=("bash",))
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("zeta"), source=ToolSource.PER_REPO)
    reg.register(_make_tool("alpha"), source=ToolSource.PER_REPO)
    schemas = reg.to_api_schema()
    names_from_schema = [s["name"] for s in schemas]
    names_from_list = [t.name for t in reg.list_tools()]
    assert names_from_schema == names_from_list


def test_to_api_schema_emits_required_keys() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("only"), source=ToolSource.DEFAULT)
    schemas = reg.to_api_schema()
    assert len(schemas) == 1
    assert set(schemas[0].keys()) == {"name", "description", "input_schema"}


def test_len_and_contains() -> None:
    reg = ToolRegistry()
    assert len(reg) == 0
    assert "bash" not in reg
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    assert len(reg) == 1
    assert "bash" in reg


def test_default_not_in_canonical_order_appended_alpha() -> None:
    """Defaults whose names are not in ``default_order`` fall to alpha tail of defaults."""
    reg = ToolRegistry(default_order=("bash",))
    reg.register(_make_tool("bash"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("zfoo"), source=ToolSource.DEFAULT)
    reg.register(_make_tool("afoo"), source=ToolSource.DEFAULT)
    names = [t.name for t in reg.list_tools()]
    assert names == ["bash", "afoo", "zfoo"]
