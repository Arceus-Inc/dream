"""Typed contracts for execute_code (no stringly tool names / bare dict access)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

EXECUTE_CODE_REGISTRY_KEY: Final[str] = "dream.execute_code.registry"

DEFAULT_TIMEOUT_SECONDS: Final[float] = 300.0
DEFAULT_MAX_TOOL_CALLS: Final[int] = 50
MAX_STDOUT_BYTES: Final[int] = 50_000
MAX_STDERR_BYTES: Final[int] = 10_000
#: Hard cap on a single incomplete RPC request line buffered in the parent.
MAX_RPC_REQUEST_BYTES: Final[int] = 1_048_576


class NestedToolName(StrEnum):
    """Dream tools allowed inside an execute_code sandbox (Hermes PTC allowlist).

    Hermes maps ``search_files``→grep/glob, ``patch``→apply_patch, ``terminal``→bash.
    ``execute_code`` itself is intentionally absent (no recursion).
    """

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    APPLY_PATCH = "apply_patch"
    GREP = "grep"
    GLOB = "glob"
    BASH = "bash"
    WEB_SEARCH = "web_search"
    WEB_EXTRACT = "web_extract"


class ExecuteCodeStatus(StrEnum):
    """Terminal status for one execute_code run."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CAP_EXCEEDED = "cap_exceeded"
    REFUSED = "refused"
    CANCELLED = "cancelled"


class RpcRequest(BaseModel):
    """One nested tool call from the child script."""

    model_config = ConfigDict(extra="forbid")

    tool: NestedToolName
    args: dict[str, Any] = Field(default_factory=dict)
    token: str = ""


class RpcResponse(BaseModel):
    """Payload returned to the child over the RPC socket."""

    model_config = ConfigDict(extra="forbid")

    content: str
    is_error: bool = False
    error: str | None = None


class ExecuteCodeOutcome(BaseModel):
    """Structured parent-facing outcome (also mirrored into ToolResult.structured)."""

    model_config = ConfigDict(extra="forbid")

    status: ExecuteCodeStatus
    output: str
    exit_code: int
    tool_calls_made: int
    duration_seconds: float
    stderr: str = ""
    summary: str = ""
    next_actions: list[str] = Field(default_factory=list)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    tool_call_log: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXECUTE_CODE_REGISTRY_KEY",
    "MAX_RPC_REQUEST_BYTES",
    "MAX_STDERR_BYTES",
    "MAX_STDOUT_BYTES",
    "ExecuteCodeOutcome",
    "ExecuteCodeStatus",
    "NestedToolName",
    "RpcRequest",
    "RpcResponse",
]
