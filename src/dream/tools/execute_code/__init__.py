"""Hermes-style programmatic tool calling — typed contracts.

Parent LLM sees one ``execute_code`` result (stdout + metadata). Nested tool
I/O stays on the RPC path and never becomes parent conversation messages.
"""

from __future__ import annotations

from dream.tools.execute_code._allowlist import sandbox_tools_for
from dream.tools.execute_code._invoker import RegistryToolInvoker
from dream.tools.execute_code._types import (
    EXECUTE_CODE_REGISTRY_KEY,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_TIMEOUT_SECONDS,
    ExecuteCodeOutcome,
    ExecuteCodeStatus,
    NestedToolName,
    RpcRequest,
    RpcResponse,
)

__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXECUTE_CODE_REGISTRY_KEY",
    "ExecuteCodeOutcome",
    "ExecuteCodeStatus",
    "NestedToolName",
    "RegistryToolInvoker",
    "RpcRequest",
    "RpcResponse",
    "sandbox_tools_for",
]
