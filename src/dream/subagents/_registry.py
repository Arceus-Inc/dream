"""Tier-2 shared SubagentRegistry — role-agnostic capability agents.

The agentic twin of the shared tool registry: capability agents any employee's
swarm can pull in by name (e.g. a researcher, a query orchestrator). They live
at the kernel level and are referenced by name from a role's subagent set.

Spec §02 Tier-2: shared registry, capability agents.
"""

from __future__ import annotations

from dream.subagents._declaration import Subagent


class SubagentRegistry:
    """Kernel-level registry of shared (Tier-2) subagents.

    Role-agnostic reasoning any employee's swarm pulls in by name.
    Referenced from a role's subagent set and dispatched directly by the
    parent beat.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Subagent] = {}

    def register(self, agent: Subagent) -> None:
        """Register a Tier-2 shared subagent."""
        if agent.name in self._agents:
            raise ValueError(f"Subagent {agent.name!r} already registered in the shared registry")
        self._agents[agent.name] = agent

    def get(self, name: str) -> Subagent | None:
        return self._agents.get(name)

    def list_names(self) -> list[str]:
        return list(self._agents.keys())

    def list_all(self) -> list[Subagent]:
        return list(self._agents.values())

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    def resolve(self, names: tuple[str, ...]) -> list[Subagent]:
        """Resolve a tuple of names to Subagent instances.

        Raises KeyError for any name not found.
        """
        result = []
        for name in names:
            agent = self._agents.get(name)
            if agent is None:
                raise KeyError(
                    f"Subagent {name!r} not found in the shared registry; "
                    f"available: {self.list_names()}"
                )
            result.append(agent)
        return result
