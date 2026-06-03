"""Harness facade. The single entry point to the SDK runtime.

Multiple Harness instances must coexist in the same process. Nothing
here reads from module-level state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from dream.contracts.hook import Hook
from dream.contracts.plugin import Plugin
from dream.contracts.provider import Provider
from dream.contracts.tool import Tool
from dream.session import Session, SessionOptions


@dataclass
class HarnessConfig:
    """Construction-time configuration for a Harness.

    The Harness reads only what is here. It never reads env vars or files
    on its own; use helpers in `dream.config` for those.
    """

    working_dir: Path = field(default_factory=Path.cwd)
    default_model: str | None = None
    default_provider: str | None = None
    permission_mode: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)


class Harness:
    """The SDK runtime facade.

    Construct with a `HarnessConfig`, register providers / tools / hooks
    / plugins, then `start_session()` to converse. Use as an async
    context manager for deterministic cleanup.
    """

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self.config = config or HarnessConfig()
        self._providers: dict[str, Provider] = {}
        self._tools: dict[str, Tool] = {}
        self._hooks: list[Hook] = []
        self._plugins: list[Plugin] = []
        self._closed = False

    # -- registration -----------------------------------------------------

    def register_provider(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def register_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_hook(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def register_plugin(self, plugin: Plugin) -> None:
        self._plugins.append(plugin)
        for tool in plugin.tools:
            self.register_tool(tool)
        for hook in plugin.hooks:
            self.register_hook(hook)
        for provider in plugin.providers:
            self.register_provider(provider)

    # -- sessions ---------------------------------------------------------

    async def start_session(self, options: SessionOptions | None = None) -> Session:
        """Create a new Session. Engine binding lands later."""
        import uuid

        return Session(id=uuid.uuid4().hex, options=options)

    # -- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


__all__ = ["Harness", "HarnessConfig"]
