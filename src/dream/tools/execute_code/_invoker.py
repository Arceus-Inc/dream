"""Registry-backed nested tool invoker for execute_code RPC."""

from __future__ import annotations

from typing import Any, Protocol

from dream.contracts.tool import ToolResult
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry
from dream.tools.execute_code._types import NestedToolName


class ToolInvoker(Protocol):
    """Dispatches an allowlisted nested tool call."""

    @property
    def calls_made(self) -> int: ...

    @property
    def cap_exceeded(self) -> bool: ...

    async def invoke(self, tool: NestedToolName, args: dict[str, Any]) -> ToolResult: ...


class RegistryToolInvoker:
    """Invoke registry tools under an allowlist + max-call cap.

    Bypasses the engine permission gate: the sandbox allowlist *is* the gate
    for nested calls. Path confinement and tool-local validation still apply.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        context: ToolExecutionContext,
        allowed: frozenset[NestedToolName],
        max_calls: int,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self._registry = registry
        self._context = context
        self._allowed = allowed
        self._max_calls = max_calls
        self._calls_made = 0
        self._cap_exceeded = False

    @property
    def calls_made(self) -> int:
        return self._calls_made

    @property
    def cap_exceeded(self) -> bool:
        return self._cap_exceeded

    async def invoke(self, tool: NestedToolName, args: dict[str, Any]) -> ToolResult:
        if tool not in self._allowed:
            available = ", ".join(sorted(t.value for t in self._allowed)) or "(none)"
            raise PermissionError(
                f"Tool '{tool.value}' is not available in execute_code. Available: {available}"
            )
        if self._calls_made >= self._max_calls:
            self._cap_exceeded = True
            return ToolResult(
                content=(
                    f"Tool call limit reached ({self._max_calls}). "
                    "Reduce the number of nested tool calls."
                ),
                is_error=True,
                metadata={"status": "cap_exceeded"},
            )

        registered = self._registry.get(tool.value)
        if registered is None:
            raise PermissionError(f"Tool '{tool.value}' is not registered in this session")

        self._calls_made += 1
        return await registered.execute(args, self._context)


__all__ = ["RegistryToolInvoker", "ToolInvoker"]
