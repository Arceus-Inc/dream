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
from dream.security import SecretProxy
from dream.tools._base import BaseTool, ToolDeclaration
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
