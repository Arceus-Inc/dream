"""Tool Protocol, result type, and execution context.

A Tool is a unit of capability the agent can invoke. The Protocol stays
minimal so external authors can implement it without subclassing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a single tool invocation.

    `content` is the human / model facing string. `structured` carries
    machine-readable output for hooks or downstream tools. `is_error`
    flags failures without raising, so the agent can observe and adapt.
    """

    content: str
    structured: dict[str, Any] | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ToolContext(Protocol):
    """Per-invocation context handed to a Tool by the Harness.

    Implementations live in `dream._internal` / `dream.tools`; consumers
    only depend on this Protocol.
    """

    @property
    def working_dir(self) -> Path: ...

    @property
    def session_id(self) -> str: ...

    @property
    def cancel_requested(self) -> bool: ...

    async def run_subprocess(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ToolResult: ...

    async def spill_large_output(self, content: str | bytes) -> str:
        """Persist a large payload and return a reference token."""
        ...


@runtime_checkable
class Tool(Protocol):
    """A capability the agent can invoke."""

    name: str
    description: str

    def input_schema(self) -> dict[str, Any]:
        """Return the JSON schema for this tool's input."""
        ...

    def is_read_only(self) -> bool:
        """True if the tool never mutates external state."""
        ...

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Run the tool against the given input and context."""
        ...
