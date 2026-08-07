"""EngineToolDispatcher wiring for ``SecretProxy``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.contracts.tool import ToolResult
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.hooks import HookExecutor
from dream.permissions import Outcome, PermissionDecision
from dream.security import SecretProxy
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource

_SECRET = "sk-live-SUPERSECRETVALUE"


class _ApiKeyInput(BaseModel):
    api_key: str = Field(..., min_length=1)


class _EchoApiKeyTool(BaseTool):
    name = "echo_api_key"
    description = "Echo api_key back in the result body."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _ApiKeyInput

    last_received: str | None = None

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        type(self).last_received = str(input["api_key"])
        return ToolResult(content=f"received api_key={input['api_key']}")


class _PathTool(BaseTool):
    name = "path_tool"
    description = "Uses a path."
    declaration = ToolDeclaration(risk="mutating", tier_required=0, timeout_seconds=5.0)
    input_model = _ApiKeyInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="executed")

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        return ToolEffects(target_paths=(Path(str(input["api_key"])),))


class _PreToolRecordingHook:
    def __init__(self) -> None:
        self.spec = HookSpec(events=(HookEvent.PRE_TOOL_USE,))
        self.seen_input: dict[str, Any] | None = None

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        if event is HookEvent.PRE_TOOL_USE:
            self.seen_input = dict(payload["tool_input"])
        return HookResult()


def _registry(tool: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool, source=ToolSource.DEFAULT)
    return reg


@pytest.fixture(autouse=True)
def _reset_tool_state() -> None:
    _EchoApiKeyTool.last_received = None


async def test_dispatcher_resolves_for_execute_and_redacts_result(tmp_path: Path) -> None:
    proxy = SecretProxy(token_factory=lambda: "fixed")
    placeholder = proxy.register("api_key", _SECRET)
    tool = _EchoApiKeyTool()
    disp = EngineToolDispatcher(
        registry=_registry(tool),
        working_dir=tmp_path,
        session_id="s",
        secret_proxy=proxy,
    )

    content, is_error = await disp.dispatch("echo_api_key", {"api_key": placeholder})

    assert is_error is False
    assert _SECRET not in content
    assert placeholder in content
    assert tool.last_received == _SECRET


async def test_dispatcher_without_proxy_unchanged(tmp_path: Path) -> None:
    tool = _EchoApiKeyTool()
    disp = EngineToolDispatcher(
        registry=_registry(tool),
        working_dir=tmp_path,
        session_id="s",
    )

    content, is_error = await disp.dispatch("echo_api_key", {"api_key": _SECRET})

    assert is_error is False
    assert _SECRET in content
    assert tool.last_received == _SECRET


async def test_pre_tool_use_sees_placeholder_not_resolved(tmp_path: Path) -> None:
    proxy = SecretProxy(token_factory=lambda: "fixed")
    placeholder = proxy.register("api_key", _SECRET)
    hook = _PreToolRecordingHook()
    disp = EngineToolDispatcher(
        registry=_registry(_EchoApiKeyTool()),
        working_dir=tmp_path,
        session_id="s",
        secret_proxy=proxy,
        hook_executor=HookExecutor([hook]),
    )

    await disp.dispatch("echo_api_key", {"api_key": placeholder})

    assert hook.seen_input is not None
    assert hook.seen_input["api_key"] == placeholder
    assert _SECRET not in hook.seen_input["api_key"]


async def test_permission_gate_sees_resolved_secret_input(tmp_path: Path) -> None:
    proxy = SecretProxy(token_factory=lambda: "fixed")
    raw_path = str(Path.home() / ".ssh" / "id_rsa")
    placeholder = proxy.register("api_key", raw_path)
    seen: list[Path] = []

    def deny(request: Any) -> PermissionDecision:
        seen.extend(request.target_paths)
        return PermissionDecision(Outcome.DENY, "blocked", "test")

    disp = EngineToolDispatcher(
        registry=_registry(_PathTool()),
        working_dir=tmp_path,
        session_id="s",
        secret_proxy=proxy,
        permission_gate=deny,
    )

    _content, is_error = await disp.dispatch("path_tool", {"api_key": placeholder})

    assert is_error
    assert seen == [Path(raw_path)]


async def test_redacts_structured_result_before_retaining_it(tmp_path: Path) -> None:
    proxy = SecretProxy(token_factory=lambda: "fixed")
    placeholder = proxy.register("api_key", _SECRET)
    disp = EngineToolDispatcher(
        registry=_registry(_EchoApiKeyTool()),
        working_dir=tmp_path,
        session_id="s",
        secret_proxy=proxy,
    )

    outcome = disp._offload_and_record(
        "echo_api_key",
        ToolResult(
            content=_SECRET,
            structured={"nested": [_SECRET, {"value": _SECRET}]},
        ),
        is_read_only=True,
        elapsed=0.0,
    )

    assert outcome.tool_result is not None
    assert outcome.tool_result.content == placeholder
    assert outcome.tool_result.structured == {
        "nested": [placeholder, {"value": placeholder}]
    }
