"""Spec 10-H — REPL ``build_default_harness`` honours role manifests.

When a caller stamps ``options.metadata["dream.role_manifest"]`` on a
:class:`SessionOptions` (the runner does this in ``open_role_session``),
the engine factory must:

1. Compute the minimum toolset by intersecting the manifest with the
   registered tools and the active sandbox tier
   (:func:`dream.roles.compute_minimum_toolset`).
2. Hand the resulting frozenset to ``build_query_engine`` as
   ``role_allowed_tools`` so the dispatcher hard-refuses every other
   tool *before* the permission gate.
3. NOT pass it to ``make_permission_gate`` as ``tool_allow``: that is an
   allow-list override that would *widen* role tools past the gate's deny
   pipeline rather than restrict them (see ``compute_session_role_allowlist``).
   Role enforcement is the dispatcher's hard deny; the gate still applies its
   full pipeline (path/command deny, tier, trust) to every role-allowed tool.

Existing call sites that DON'T stamp the manifest stay unconstrained.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from dream import _factory as factory_module
from dream.contracts.tool import ToolResult
from dream.engine._engine import QueryEngine
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.permissions import Outcome, PermissionRequest
from dream.repl._session import build_default_harness
from dream.roles import RoleManifest
from dream.runner._role_session import ROLE_MANIFEST_METADATA_KEY
from dream.session import SessionOptions
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _NopInput(BaseModel):
    pass


class _ReadTool(BaseTool):
    name = "file_read"
    description = "Read a file."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _NopInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(content="ok")


class _WriteTool(BaseTool):
    name = "file_write"
    description = "Write a file."
    declaration = ToolDeclaration(risk="mutating", tier_required=2, timeout_seconds=5.0)
    input_model = _NopInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(content="ok")


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_ReadTool(), source=ToolSource.DEFAULT)
    reg.register(_WriteTool(), source=ToolSource.DEFAULT)
    return reg


def _env() -> dict[str, str]:
    return {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_SMOKE_BASE_URL": "http://127.0.0.1:9/v1",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_factory_without_role_metadata_leaves_dispatcher_unconstrained(
    tmp_path: Path,
) -> None:
    """No role stamp ⇒ legacy behaviour: dispatcher has no allowlist."""
    harness = build_default_harness(
        env=_env(), working_dir=tmp_path, registry=_registry()
    )
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("sid", SessionOptions())
    assert isinstance(engine, QueryEngine)
    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    assert engine.dispatcher.role_allowed_tools is None


def test_factory_with_role_manifest_intersects_to_minimum_toolset(
    tmp_path: Path,
) -> None:
    """A reviewer manifest listing only ``file_read`` ⇒ dispatcher refuses
    every other tool, even ``file_write`` that the gate would otherwise
    consider given a high enough tier."""
    manifest = RoleManifest(
        name="evaluator",
        description="Reads only.",
        system_prompt="You evaluate.",
        tools=("file_read",),
    )
    options = SessionOptions(metadata={ROLE_MANIFEST_METADATA_KEY: manifest})
    harness = build_default_harness(
        env=_env(), working_dir=tmp_path, registry=_registry()
    )
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("sid", options)
    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    # The dispatcher's allowlist is computed by intersection: the manifest
    # names ``file_read`` AND the active sandbox tier permits tier-0 reads.
    assert engine.dispatcher.role_allowed_tools == frozenset({"file_read"})


def test_factory_with_manifest_drops_tools_above_active_tier(
    tmp_path: Path,
) -> None:
    """A manifest may name a write tool, but if the active sandbox tier
    sits below the tool's ``tier_required`` the minimum toolset drops it.
    The default REPL sandbox is READ_ONLY (tier 0), so ``file_write``
    (tier 2) must NOT survive even when explicitly allow-listed."""
    manifest = RoleManifest(
        name="evaluator",
        description="Tries to write but tier won't let it.",
        system_prompt="You evaluate.",
        tools=("file_read", "file_write"),
    )
    options = SessionOptions(metadata={ROLE_MANIFEST_METADATA_KEY: manifest})
    harness = build_default_harness(
        env=_env(), working_dir=tmp_path, registry=_registry()
    )
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("sid", options)
    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    # ``file_write`` declared tier 2 > active READ_ONLY tier 0 ⇒ dropped.
    assert engine.dispatcher.role_allowed_tools == frozenset({"file_read"})


def test_factory_does_not_feed_role_allowlist_into_gate_tool_allow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SECURITY: the role allow-list reaches the *dispatcher* (a hard deny) but
    must NEVER reach the gate's ``tool_allow``.

    ``tool_allow`` is an allow-list override — it lets a tool bypass the
    tool-deny list, so feeding role tools there would *widen* a role past an
    operator deny rather than restrict it. Role enforcement belongs in
    ``EngineToolDispatcher.role_allowed_tools``; the gate must keep applying its
    full pipeline to every role-allowed tool. We spy on ``make_permission_gate``
    and assert it is never handed a ``tool_allow`` set.
    """
    real_make_gate = factory_module.make_permission_gate
    seen_tool_allow: list[frozenset[str] | None] = []

    def _spy(
        registry: ToolRegistry,
        *,
        paths: Any,
        cwd: Any,
        tool_allow: frozenset[str] | None = None,
        clock: Any = None,
    ) -> Any:
        seen_tool_allow.append(tool_allow)
        return real_make_gate(
            registry, paths=paths, cwd=cwd, tool_allow=tool_allow, clock=clock
        )

    monkeypatch.setattr(factory_module, "make_permission_gate", _spy)

    manifest = RoleManifest(
        name="evaluator",
        description="Reads only.",
        system_prompt="You evaluate.",
        tools=("file_read",),
    )
    options = SessionOptions(metadata={ROLE_MANIFEST_METADATA_KEY: manifest})
    harness = build_default_harness(
        env=_env(), working_dir=tmp_path, registry=_registry()
    )
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("sid", options)
    # The role boundary IS enforced — but via the dispatcher, not the gate.
    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    assert engine.dispatcher.role_allowed_tools == frozenset({"file_read"})
    # The factory built a gate, and never with a tool_allow override.
    assert seen_tool_allow, "factory did not build a permission gate"
    assert all(tool_allow is None for tool_allow in seen_tool_allow)


def test_role_allowed_tool_still_subject_to_gate_command_deny(
    tmp_path: Path,
) -> None:
    """End-to-end contract: a role-allowed tool issuing a dangerous command is
    still DENIED by the gate's command-deny step — it is not short-circuited to
    ALLOW just because the role lists it.

    ``file_read`` is on the role allow-list, yet an ``rm -rf /`` command must hit
    command-deny. (command-deny runs before any tier grant, so this holds at
    every sandbox tier.)
    """
    manifest = RoleManifest(
        name="evaluator",
        description="Reads only, but the gate still guards every call.",
        system_prompt="You evaluate.",
        tools=("file_read",),
    )
    options = SessionOptions(metadata={ROLE_MANIFEST_METADATA_KEY: manifest})
    harness = build_default_harness(
        env=_env(), working_dir=tmp_path, registry=_registry()
    )
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("sid", options)
    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    assert engine.dispatcher.role_allowed_tools == frozenset({"file_read"})
    gate = engine.dispatcher.permission_gate
    assert gate is not None
    decision = gate(
        PermissionRequest(
            tool_name="file_read",
            is_read_only=False,
            command="rm -rf /",
        )
    )
    assert decision.outcome is Outcome.DENY
    assert decision.rule == "command_deny"
