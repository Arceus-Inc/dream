"""Plugin manifest parsing + validation (spec 13 §Artefact shapes).

``plugins/{name}/manifest.toml`` parses into the cross-repo
:class:`dream.contracts.plugin.PluginManifest`. Validation is strict and
front-loaded: a malformed manifest is refused before any code from the
plugin directory is imported.
"""

from __future__ import annotations

import tomllib

from dream.contracts.plugin import PluginManifest
from dream.errors import PluginError

__all__ = ["KNOWN_CAPABILITIES", "PluginManifestError", "parse_manifest"]

# The capability vocabulary is closed (spec 13 decision #18): an unknown
# capability is a typo or an escalation attempt, not a forward-compat case.
KNOWN_CAPABILITIES = frozenset({"repo-write", "network", "subprocess"})


class PluginManifestError(PluginError):
    """A manifest.toml failed schema validation."""

    code = "dream.plugin.manifest"


def parse_manifest(text: str) -> PluginManifest:
    """Parse + validate one ``manifest.toml`` body."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PluginManifestError(f"manifest is not valid TOML: {exc}") from exc

    name = _required_str(data, "name")
    if "/" in name or "\\" in name or name.startswith("."):
        raise PluginManifestError(
            f"plugin name must be a plain directory name, got {name!r}"
        )
    version = _required_str(data, "version")
    entry = _required_str(data, "entry")
    if "/" in entry or "\\" in entry or entry.startswith("."):
        raise PluginManifestError(
            f"entry must be a file inside the plugin dir, got {entry!r}"
        )

    capabilities = _capabilities(data)
    subscribes = _str_tuple(data.get("subscribes", {}).get("hooks", ()))
    return PluginManifest(
        name=name,
        version=version,
        description=str(data.get("description", "")),
        entry=entry,
        homepage=data.get("homepage"),
        requires=_str_tuple(data.get("requires", ())),
        metadata={"capabilities": capabilities, "subscribes": subscribes},
    )


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PluginManifestError(f"manifest requires a non-empty {key!r} string")
    return value


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PluginManifestError(f"expected a list of strings, got {value!r}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PluginManifestError(f"expected a string, got {item!r}")
        out.append(item)
    return tuple(out)


def _capabilities(data: dict[str, object]) -> tuple[str, ...]:
    section = data.get("capabilities", {})
    if not isinstance(section, dict):
        raise PluginManifestError("[capabilities] must be a table")
    required = _str_tuple(section.get("required", ()))
    unknown = sorted(set(required) - KNOWN_CAPABILITIES)
    if unknown:
        raise PluginManifestError(
            f"unknown capability {unknown[0]!r}; "
            f"known: {sorted(KNOWN_CAPABILITIES)}"
        )
    return required
