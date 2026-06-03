"""Plugin manifest and Plugin descriptor.

A Plugin is a bundle of tools, hooks, skills, agents, and providers
contributed by a third party. The on-disk `plugin.json` parses into a
`PluginManifest`; the loaded module yields a `Plugin` instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dream.contracts.hook import Hook
from dream.contracts.provider import Provider
from dream.contracts.skill import Skill
from dream.contracts.tool import Tool


@dataclass(frozen=True)
class PluginManifest:
    """Parsed `plugin.json`. Stable across plugin loaders."""

    name: str
    version: str
    description: str = ""
    entry: str = ""
    homepage: str | None = None
    requires: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plugin:
    """A loaded plugin and its contributions to the Harness."""

    manifest: PluginManifest
    tools: tuple[Tool, ...] = ()
    hooks: tuple[Hook, ...] = ()
    skills: tuple[Skill, ...] = ()
    providers: tuple[Provider, ...] = ()
