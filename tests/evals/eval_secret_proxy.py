"""Eval: secrets never appear in transcript-facing tool results after redaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.security import SecretProxy
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource

_SECRET = "sk-live-SUPERSECRETVALUE"


class _ApiKeyInput(BaseModel):
    api_key: str = Field(..., min_length=1)


class _EchoApiKeyTool(BaseTool):
    name = "echo_api_key"
    description = "Echo api_key into tool output."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _ApiKeyInput

    captured: str | None = None

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        type(self).captured = str(input["api_key"])
        return ToolResult(content=f"tool saw api_key={input['api_key']}")


@pytest.fixture(autouse=True)
def _reset_captured() -> None:
    _EchoApiKeyTool.captured = None


@pytest.mark.eval
async def test_secret_never_leaks_to_model_transcript(tmp_path: Path) -> None:
    proxy = SecretProxy(token_factory=lambda: "evaltoken")
    placeholder = proxy.register("api_key", _SECRET)
    reg = ToolRegistry()
    reg.register(_EchoApiKeyTool(), source=ToolSource.DEFAULT)
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="eval-secret-proxy",
        secret_proxy=proxy,
    )

    content, is_error = await disp.dispatch("echo_api_key", {"api_key": placeholder})

    assert is_error is False
    assert "SUPERSECRETVALUE" not in content
    assert _SECRET not in content
    assert placeholder in content
    assert _EchoApiKeyTool.captured == _SECRET
