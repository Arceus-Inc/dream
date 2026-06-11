"""Plugins — repo-local, opt-in, capability-gated extension bundles (spec 13).

``plugins/{name}/manifest.toml`` + an entry module exporting
``get_plugin(manifest) -> Plugin``. Loading is opt-in via
``.harness/plugins-enabled.toml`` and tier-gated: a plugin whose declared
capabilities exceed the session's sandbox tier is refused. A plugin
failure never aborts the caller. Register the loaded bundles with
:meth:`dream.Harness.register_plugin`.
"""

from __future__ import annotations

from dream.plugins._loader import (
    FailedPlugin,
    PluginLoadReport,
    load_enabled_plugins,
    read_enabled_names,
)
from dream.plugins._schemas import (
    KNOWN_CAPABILITIES,
    PluginManifestError,
    parse_manifest,
)

__all__ = [
    "KNOWN_CAPABILITIES",
    "FailedPlugin",
    "PluginLoadReport",
    "PluginManifestError",
    "load_enabled_plugins",
    "parse_manifest",
    "read_enabled_names",
]
