"""Plugin discovery + loading (spec 13 §Behaviours "Plugin loading").

The flow, per spec:

1. Read ``.harness/plugins-enabled.toml`` — loading is opt-in; a plugin
   on disk but not listed is ignored. Missing file → no plugins.
2. For each enabled name: locate ``plugins/{name}/``, parse + validate
   ``manifest.toml`` (malformed → refuse this plugin, continue).
3. Capability-gate against the active sandbox tier — a plugin whose
   declared capabilities exceed the tier is refused with an error
   naming the capability.
4. Import the entry file and call its ``get_plugin(manifest)`` under an
   init timeout. Exception or timeout → failed-to-load, continue.

A plugin failure never aborts the caller (decision #22). Version-pin
mismatch against the on-disk manifest warns, never errors.
"""

from __future__ import annotations

import importlib.util
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dream.contracts.plugin import Plugin
from dream.plugins._schemas import PluginManifestError, parse_manifest

__all__ = [
    "FailedPlugin",
    "PluginLoadReport",
    "load_enabled_plugins",
    "read_enabled_names",
]

_INIT_TIMEOUT_SECONDS = 5.0

# Which capabilities each sandbox tier (spec 13B) may grant to a plugin.
_TIER_CAPABILITIES: dict[str, frozenset[str]] = {
    "read-only": frozenset(),
    "repo-write": frozenset({"repo-write"}),
    "workspace-net": frozenset({"repo-write", "network", "subprocess"}),
    "trusted": frozenset({"repo-write", "network", "subprocess"}),
}


@dataclass(frozen=True)
class FailedPlugin:
    """One plugin that did not load, and why."""

    name: str
    reason: str


@dataclass(frozen=True)
class PluginLoadReport:
    """The outcome of one ``load_enabled_plugins`` pass."""

    loaded: tuple[Plugin, ...] = ()
    failed: tuple[FailedPlugin, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


def read_enabled_names(repo: Path) -> tuple[tuple[str, str | None], ...]:
    """Return ``(name, version_pin)`` pairs from ``.harness/plugins-enabled.toml``."""
    enabled_path = repo / ".harness" / "plugins-enabled.toml"
    if not enabled_path.exists():
        return ()
    try:
        data = tomllib.loads(enabled_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    entries = data.get("plugin", [])
    if not isinstance(entries, list):
        return ()
    out: list[tuple[str, str | None]] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            pin = entry.get("version")
            out.append((entry["name"], pin if isinstance(pin, str) else None))
    return tuple(out)


def load_enabled_plugins(
    repo: Path,
    *,
    tier: str,
    init_timeout_seconds: float = _INIT_TIMEOUT_SECONDS,
) -> PluginLoadReport:
    """Load every enabled plugin under ``repo/plugins/``, tier-gated."""
    loaded: list[Plugin] = []
    failed: list[FailedPlugin] = []
    warnings: list[str] = []
    allowed = _TIER_CAPABILITIES.get(tier, frozenset())
    for name, pin in read_enabled_names(repo):
        outcome = _load_one(
            repo,
            name,
            pin=pin,
            allowed=allowed,
            init_timeout_seconds=init_timeout_seconds,
            warnings=warnings,
        )
        if isinstance(outcome, Plugin):
            loaded.append(outcome)
        else:
            failed.append(outcome)
    return PluginLoadReport(
        loaded=tuple(loaded), failed=tuple(failed), warnings=tuple(warnings)
    )


def _load_one(
    repo: Path,
    name: str,
    *,
    pin: str | None,
    allowed: frozenset[str],
    init_timeout_seconds: float,
    warnings: list[str],
) -> Plugin | FailedPlugin:
    plugin_dir = repo / "plugins" / name
    manifest_path = plugin_dir / "manifest.toml"
    if not manifest_path.exists():
        return FailedPlugin(name=name, reason=f"no manifest at {manifest_path}")
    try:
        manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except (OSError, PluginManifestError) as exc:
        return FailedPlugin(name=name, reason=str(exc))
    if manifest.name != name:
        return FailedPlugin(
            name=name,
            reason=f"manifest name {manifest.name!r} does not match directory {name!r}",
        )
    if pin is not None and pin != manifest.version:
        warnings.append(
            f"plugin {name}: enabled version pin {pin} != manifest {manifest.version}"
        )
    capabilities = manifest.metadata.get("capabilities", ())
    excess = sorted(set(capabilities) - allowed)
    if excess:
        return FailedPlugin(
            name=name,
            reason=(
                f"capability {excess[0]!r} exceeds the active sandbox tier; "
                "raise the tier or drop the capability"
            ),
        )
    return _import_entry(
        manifest, plugin_dir, name=name, init_timeout_seconds=init_timeout_seconds
    )


def _import_entry(
    manifest: object,
    plugin_dir: Path,
    *,
    name: str,
    init_timeout_seconds: float,
) -> Plugin | FailedPlugin:
    """Import ``entry`` and call ``get_plugin(manifest)`` under a wall clock.

    The import itself is synchronous (Python gives no safe preemption);
    the timeout is checked after — an over-budget init is reported as
    ``init_timeout`` even though it ran to completion, matching the
    spec's "mark failed-to-load" semantics without killing the process.
    """
    from dream.contracts.plugin import PluginManifest

    assert isinstance(manifest, PluginManifest)
    entry_path = plugin_dir / manifest.entry
    if not entry_path.exists():
        return FailedPlugin(name=name, reason=f"entry file missing: {entry_path}")
    module_name = f"dream_plugin_{name.replace('-', '_')}"
    started = time.monotonic()
    try:
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            return FailedPlugin(name=name, reason=f"cannot import {entry_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        get_plugin = getattr(module, "get_plugin", None)
        if get_plugin is None:
            return FailedPlugin(
                name=name, reason="entry module does not export get_plugin(manifest)"
            )
        plugin = get_plugin(manifest)
    except Exception as exc:
        return FailedPlugin(name=name, reason=f"init raised: {exc}")
    elapsed = time.monotonic() - started
    if elapsed > init_timeout_seconds:
        return FailedPlugin(
            name=name, reason=f"init_timeout: took {elapsed:.1f}s"
        )
    if not isinstance(plugin, Plugin):
        return FailedPlugin(
            name=name,
            reason=f"get_plugin returned {type(plugin).__name__}, expected Plugin",
        )
    return plugin
