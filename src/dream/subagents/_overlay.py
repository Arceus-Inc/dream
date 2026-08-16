"""Tighten-only permission overlay for child subagent sessions.

An overlay only *removes* capabilities. It cannot grant write, network,
execute, or tools the parent gate would deny.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "EXECUTE_TOOLS",
    "OverlayCapability",
    "PermissionOverlay",
]


class OverlayCapability(StrEnum):
    """Named capabilities an overlay may strip from the parent."""

    WRITE = "write"
    NETWORK = "network"
    EXECUTE = "execute"


_WRITE_ALIASES: frozenset[str] = frozenset(
    {
        OverlayCapability.WRITE.value,
        "repo-write",
        "repo-write+net-allowlist",
    }
)
_NETWORK_ALIASES: frozenset[str] = frozenset(
    {
        OverlayCapability.NETWORK.value,
        "net",
        "net-allowlist",
    }
)
_EXECUTE_ALIASES: frozenset[str] = frozenset(
    {
        OverlayCapability.EXECUTE.value,
        "exec",
    }
)

EXECUTE_TOOLS: frozenset[str] = frozenset({"bash", "execute_code", "run_command"})


@dataclass(frozen=True, slots=True)
class PermissionOverlay:
    """Capabilities and tools to remove from the parent. Never grants."""

    write: bool = False
    network: bool = False
    execute: bool = False
    tools: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, raw: object) -> PermissionOverlay:
        """Build an overlay from tokens or return ``raw`` unchanged.

        Known capability tokens (``write`` / ``network`` / ``execute`` and
        sandbox-tier aliases) set flags. Every other token is a tool name to
        deny. Unknown tokens never become grants.
        """
        if raw is None:
            return cls()
        if isinstance(raw, PermissionOverlay):
            return raw
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise TypeError("permission overlay must be a sequence of tokens, not a bare string")
        write = False
        network = False
        execute = False
        tools: set[str] = set()
        for token in raw:
            name = str(token).strip()
            if not name:
                continue
            if name in _WRITE_ALIASES:
                write = True
            elif name in _NETWORK_ALIASES:
                network = True
            elif name in _EXECUTE_ALIASES:
                execute = True
            else:
                tools.add(name)
        return cls(write=write, network=network, execute=execute, tools=frozenset(tools))

    def as_tokens(self) -> tuple[str, ...]:
        tokens: list[str] = []
        if self.write:
            tokens.append(OverlayCapability.WRITE.value)
        if self.network:
            tokens.append(OverlayCapability.NETWORK.value)
        if self.execute:
            tokens.append(OverlayCapability.EXECUTE.value)
        tokens.extend(sorted(self.tools))
        return tuple(tokens)

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_tokens())

    def __contains__(self, token: object) -> bool:
        if not isinstance(token, str):
            return False
        if token in _WRITE_ALIASES:
            return self.write
        if token in _NETWORK_ALIASES:
            return self.network
        if token in _EXECUTE_ALIASES:
            return self.execute
        return token in self.tools

    def __bool__(self) -> bool:
        return self.write or self.network or self.execute or bool(self.tools)
