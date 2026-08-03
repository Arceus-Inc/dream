"""Registry-backed nested tool invoker for execute_code RPC."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from dream.contracts.tool import ToolResult
from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry
from dream.tools.execute_code._types import NestedToolName

PermissionGate = Callable[[PermissionRequest], PermissionDecision]


class ToolInvoker(Protocol):
    """Dispatches an allowlisted nested tool call."""

    @property
    def calls_made(self) -> int: ...

    @property
    def cap_exceeded(self) -> bool: ...

    async def invoke(self, tool: NestedToolName, args: dict[str, Any]) -> ToolResult: ...


class RegistryToolInvoker:
    """Invoke registry tools under allowlist + permission gate + max-call cap.

    Nested calls must pass the same role allowlist and Spec 13C permission gate
    as a top-level dispatch — registry membership alone is not enough.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        context: ToolExecutionContext,
        allowed: frozenset[NestedToolName],
        max_calls: int,
        role_allowed_tools: frozenset[str] | None = None,
        permission_gate: PermissionGate | None = None,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self._registry = registry
        self._context = context
        self._allowed = allowed
        self._max_calls = max_calls
        self._role_allowed_tools = role_allowed_tools
        self._permission_gate = permission_gate
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
        if self._role_allowed_tools is not None and tool.value not in self._role_allowed_tools:
            raise PermissionError(
                f"Tool '{tool.value}' is not in this role's manifest allowlist."
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

        try:
            registered.input_model.model_validate(args)
        except ValidationError as exc:
            return ToolResult(
                content=f"Invalid input for {tool.value}: {exc}",
                is_error=True,
                metadata={"status": "schema_invalid"},
            )

        if self._permission_gate is not None:
            is_read_only = registered.is_read_only_for(args)
            effects = registered.effects_for(args)
            target_paths = effects.target_paths
            if not is_read_only and not target_paths and effects.network_host is None:
                target_paths = (self._context.working_dir,)
            decision = self._permission_gate(
                PermissionRequest(
                    tool_name=tool.value,
                    is_read_only=is_read_only,
                    target_paths=tuple(Path(p) for p in target_paths),
                    command=effects.command,
                    network_host=effects.network_host,
                )
            )
            if not decision.allowed:
                reason = decision.reason
                if decision.outcome is Outcome.ASK:
                    reason = f"{reason} (approval required; nested execute_code treats ASK as deny)"
                return ToolResult(
                    content=f"Permission denied for {tool.value!r}: {reason}",
                    is_error=True,
                    metadata={"status": "permission_denied", "rule": decision.rule},
                )

        self._calls_made += 1
        return await registered.execute(args, self._context)


__all__ = ["PermissionGate", "RegistryToolInvoker", "ToolInvoker"]
