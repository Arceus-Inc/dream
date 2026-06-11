"""The ``build_harness`` async opener — MCP + plugins wired on first open.

MCP connect and plugin import are async/IO, so ``build_harness`` stashes them
on ``HarnessConfig._async_opener`` and the harness runs them once at the
``start_session`` chokepoint (``Harness._ensure_open``). These tests prove the
*wiring contract* offline (no live model, no real MCP server):

- both surfaces off → no opener at all (chokepoint stays a no-op);
- an empty workspace → opening is a tolerant no-op (nothing to wire);
- an enabled repo-local plugin → its ``BaseTool`` lands in the engine-visible
  ``ToolRegistry`` after the first ``start_session`` (so the model can see it);
- a plugin whose tool name collides → the whole plugin is skipped, not
  half-installed, and the open still succeeds.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from dream import build_harness
from dream.harness import Harness
from dream.tools._registry import ToolRegistry
from dream.tools.builtin import default_registry

_MANIFEST = """\
name        = "metrics-pusher"
version     = "0.1.0"
entry       = "main.py"
description = "Push session metrics."

[capabilities]
required = ["repo-write"]
"""

# A plugin entry that contributes one real ``BaseTool`` named ``metrics_push``.
_TOOL_ENTRY = '''\
from typing import Any

from pydantic import BaseModel

from dream.contracts.plugin import Plugin
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class _In(BaseModel):
    pass


class MetricsPushTool(BaseTool):
    name = "metrics_push"
    description = "Push session metrics to the collector."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="pushed")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(MetricsPushTool(),))
'''

# A plugin whose tool deliberately collides with a built-in (``bash``).
_COLLIDING_ENTRY = '''\
from typing import Any

from pydantic import BaseModel

from dream.contracts.plugin import Plugin
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class _In(BaseModel):
    pass


class ShadowBash(BaseTool):
    name = "bash"  # collides with the built-in
    description = "A shadow of the built-in bash tool."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="shadowed")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(ShadowBash(),))
'''


def _write_plugin(repo: Path, name: str, *, entry: str) -> None:
    plugin_dir = repo / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    body = _MANIFEST.replace('"metrics-pusher"', f'"{name}"')
    (plugin_dir / "manifest.toml").write_text(body, encoding="utf-8")
    (plugin_dir / "main.py").write_text(entry, encoding="utf-8")


def _enable(repo: Path, *names: str) -> None:
    enabled = repo / ".harness" / "plugins-enabled.toml"
    enabled.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'[[plugin]]\nname = "{n}"\n' for n in names)
    enabled.write_text(body, encoding="utf-8")


def _env(tmp_path: Path) -> Mapping[str, str]:
    # Isolate DREAM_HOME so task/cron sidecars never touch the real ~/.dream.
    return {"DREAM_HOME": str(tmp_path / "dreamhome")}


def _build(
    tmp_path: Path, registry: ToolRegistry, *, mcp: bool = True, plugins: bool = True
) -> Harness:
    return build_harness(
        model="probe",
        api_key="probe",
        working_dir=tmp_path,
        registry=registry,
        mcp=mcp,
        plugins=plugins,
        env=_env(tmp_path),
    )


def test_no_opener_when_both_surfaces_disabled(tmp_path: Path) -> None:
    harness = _build(tmp_path, default_registry(), mcp=False, plugins=False)
    assert harness.config._async_opener is None


def test_opener_present_when_a_surface_is_enabled(tmp_path: Path) -> None:
    harness = _build(tmp_path, default_registry(), mcp=False, plugins=True)
    assert harness.config._async_opener is not None


@pytest.mark.asyncio
async def test_empty_workspace_opens_as_a_noop(tmp_path: Path) -> None:
    registry = default_registry()
    before = {t.name for t in registry.list_tools()}
    harness = _build(tmp_path, registry)
    await harness.start_session()  # fires the opener; nothing to wire
    after = {t.name for t in registry.list_tools()}
    assert after == before
    await harness.aclose()


@pytest.mark.asyncio
async def test_enabled_plugin_tool_reaches_the_engine_registry(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "metrics-pusher", entry=_TOOL_ENTRY)
    _enable(tmp_path, "metrics-pusher")
    registry = default_registry()
    assert "metrics_push" not in registry
    harness = _build(tmp_path, registry)
    await harness.start_session()
    # The plugin tool is now visible to the per-session engine wire schema.
    assert "metrics_push" in registry
    # And recorded on the harness as a loaded plugin bundle.
    assert [p.manifest.name for p in harness._plugins] == ["metrics-pusher"]
    await harness.aclose()


@pytest.mark.asyncio
async def test_opener_runs_once_even_across_sessions(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "metrics-pusher", entry=_TOOL_ENTRY)
    _enable(tmp_path, "metrics-pusher")
    registry = default_registry()
    harness = _build(tmp_path, registry)
    await harness.start_session()
    await harness.start_session()  # second open must not re-register (no collision)
    assert sum(1 for t in registry.list_tools() if t.name == "metrics_push") == 1
    assert len(harness._plugins) == 1
    await harness.aclose()


@pytest.mark.asyncio
async def test_colliding_plugin_is_skipped_whole(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "shadow", entry=_COLLIDING_ENTRY)
    _enable(tmp_path, "shadow")
    registry = default_registry()
    builtin_bash = registry.get("bash")
    harness = _build(tmp_path, registry)
    await harness.start_session()  # must not raise on the collision
    # The built-in bash is untouched, and the plugin did not half-install.
    assert registry.get("bash") is builtin_bash
    assert harness._plugins == []
    await harness.aclose()
