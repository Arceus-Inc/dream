"""Default tool registry composition pin — Level-2 coding surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolSource
from dream.tools.builtin import (
    LEVEL2_ORDER,
    default_registry,
    register_browser_tools,
    register_code_intel_tools,
    register_cron_tools,
    register_legacy_surface,
    register_memory_tools,
    register_observability_tools,
    register_plan_tools,
    register_task_tools,
    register_web_tools,
    register_worktree_tools,
)
from dream.tools.builtin.task_get import TaskGetTool
from dream.tools.builtin.task_output import TaskOutputTool
from dream.tools.builtin.task_stop import TaskStopTool

_LEVEL2_NAMES = set(LEVEL2_ORDER)

_PACK_NAMES: frozenset[str] = frozenset(
    {
        "lsp",
        "memory_search",
        "memory_get",
        "query_logs",
        "query_metrics",
        "task_create",
        "task_get",
        "task_output",
        "task_stop",
        "task_update",
        "cron_list",
        "cron_show",
        "cron_create",
        "cron_delete",
        "cron_toggle",
        "remote_trigger",
        "enter_worktree",
        "exit_worktree",
        "plan_show",
        "web_search",
        "web_extract",
        "web_fetch",
        "browser_run",
        "execute_code",
    }
)


def test_default_registry_holds_level2_tools_only() -> None:
    reg = default_registry()
    names = [t.name for t in reg.list_tools()]
    assert set(names) == _LEVEL2_NAMES
    assert set(names).isdisjoint(_PACK_NAMES)


def test_default_registry_order_is_canonical() -> None:
    """Order is byte-stable so the model-facing API schema does not jitter."""
    reg = default_registry()
    names = [t.name for t in reg.list_tools()]
    assert names == list(LEVEL2_ORDER)


def test_level2_catalog_invariants() -> None:
    """Eval: every Level-2 tool has schema + risk/tier; packs stay out."""
    reg = default_registry()
    for tool in reg.list_tools():
        schema = tool.input_schema()
        assert isinstance(schema, dict)
        assert schema.get("type") == "object" or "properties" in schema
        assert tool.declaration.risk in {"safe", "mutating", "external"}
        assert tool.declaration.tier_required >= 0
        assert tool.declaration.timeout_seconds > 0
    assert {t.name for t in reg.list_tools()} == _LEVEL2_NAMES


def test_legacy_surface_restores_former_defaults() -> None:
    reg = default_registry()
    register_legacy_surface(reg)
    names = {t.name for t in reg.list_tools()}
    assert _LEVEL2_NAMES <= names
    assert _PACK_NAMES <= names


def test_pack_registration_is_idempotent() -> None:
    reg = default_registry()
    register_memory_tools(reg)
    register_memory_tools(reg)
    register_web_tools(reg)
    register_web_tools(reg)
    assert {t.name for t in reg.list_tools()} >= {
        "memory_search",
        "memory_get",
        "web_search",
        "web_extract",
        "web_fetch",
    }


def test_pack_registration_rejects_untrusted_collision() -> None:
    from dream.tools._registry import ToolCollisionError
    from dream.tools.builtin.web_search import WebSearchTool

    reg = default_registry()
    custom = WebSearchTool()
    reg.register(custom, source=ToolSource.PER_REPO)

    with pytest.raises(ToolCollisionError):
        register_web_tools(reg)


def test_individual_packs_do_not_cross_pollute() -> None:
    reg = default_registry()
    register_plan_tools(reg)
    names = {t.name for t in reg.list_tools()}
    assert "plan_show" in names
    assert "web_search" not in names
    assert "task_create" not in names
    assert "browser_run" not in names


def test_default_registry_tools_are_marked_default_source() -> None:
    from dream.tools._registry import ToolCollisionError

    reg = default_registry()
    tool = next(iter(reg))
    with pytest.raises(ToolCollisionError):
        reg.register(tool, source=ToolSource.PER_REPO)


def test_default_registry_is_independent_between_calls() -> None:
    a = default_registry()
    b = default_registry()
    assert a is not b
    assert [t.name for t in a] == [t.name for t in b]


def test_pack_order_stays_canonical_after_full_surface() -> None:
    reg = default_registry()
    register_legacy_surface(reg)
    names = [t.name for t in reg.list_tools()]
    # Level-2 prefix preserved; packs appear in _FULL_ORDER relative positions.
    assert names[: len(LEVEL2_ORDER)] == list(LEVEL2_ORDER)
    assert names.index("memory_search") < names.index("task_create")
    assert names.index("web_search") < names.index("execute_code")


async def test_task_tool_recovery_guidance_names_only_registered_tools(
    tmp_path: Path,
) -> None:
    """Unknown-id error guidance must point at real tools. ``task_list`` was
    never registered, so naming it traps the agent in a dead-end retry."""
    reg = default_registry()
    register_task_tools(reg)
    registered = {t.name for t in reg.list_tools()}

    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    metadata: dict[str, object] = {}
    put_task_context(metadata, TaskSessionContext(manager=manager))
    ctx = ToolExecutionContext(
        working_dir=tmp_path, session_id="s_test", metadata=metadata
    )

    for tool in (TaskGetTool(), TaskOutputTool(), TaskStopTool()):
        result = await tool.execute({"task_id": "nope"}, ctx)
        assert result.is_error is True
        retry = result.metadata["safe_retry"]
        assert "task_list" not in retry, f"{tool.name} still references task_list"
        mentioned = {name for name in registered if name in retry}
        assert mentioned, f"{tool.name} guidance names no registered tool: {retry!r}"


def test_register_helpers_cover_all_pack_names() -> None:
    """Eval: every pack helper registers a disjoint non-empty subset."""
    checkers = (
        (register_memory_tools, {"memory_search", "memory_get"}),
        (register_task_tools, {"task_create", "task_get", "task_output", "task_stop", "task_update"}),
        (
            register_cron_tools,
            {
                "cron_list",
                "cron_show",
                "cron_create",
                "cron_delete",
                "cron_toggle",
                "remote_trigger",
            },
        ),
        (register_web_tools, {"web_search", "web_extract", "web_fetch"}),
        (register_browser_tools, {"browser_run"}),
        (register_observability_tools, {"query_logs", "query_metrics"}),
        (register_worktree_tools, {"enter_worktree", "exit_worktree"}),
        (register_code_intel_tools, {"lsp", "execute_code"}),
        (register_plan_tools, {"plan_show"}),
    )
    seen: set[str] = set()
    for register, expected in checkers:
        reg = default_registry()
        register(reg)
        got = {t.name for t in reg.list_tools()} - _LEVEL2_NAMES
        assert got == expected
        assert seen.isdisjoint(expected)
        seen |= expected
    assert seen == _PACK_NAMES
