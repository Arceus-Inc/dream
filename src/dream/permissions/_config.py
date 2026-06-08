"""Operator sandbox posture config — ``.harness/sandbox.toml`` (Spec 13B).

Loads the session tier, repo-write escape-hatch roots, and add-only credential
patterns into a :class:`SandboxConfig`. The ``unrestricted`` tier is doubly
gated: ``tier = "unrestricted"`` also requires ``confirm_unrestricted = true``,
else the load fails — disabling the write boundary and network gating must be a
deliberate, reviewable act. A missing file yields safe defaults (``repo-write``);
a malformed or unsafe file fails fast.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dream.permissions._types import SandboxTier


class SandboxConfigError(ValueError):
    """Raised when ``sandbox.toml`` is malformed or unsafely configured."""


@dataclass(frozen=True)
class SandboxConfig:
    """The operator's sandbox posture, parsed from ``sandbox.toml``."""

    tier: SandboxTier = SandboxTier.REPO_WRITE
    extra_allowed: tuple[str, ...] = ()
    credential_extra: tuple[str, ...] = ()


def parse_sandbox_config(text: str) -> SandboxConfig:
    """Parse the config body into a :class:`SandboxConfig`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SandboxConfigError(f"invalid sandbox TOML: {exc}") from exc

    return SandboxConfig(
        tier=_parse_tier(data),
        extra_allowed=_string_list(data, "extra_allowed"),
        credential_extra=_string_list(data, "credential_extra"),
    )


def read_sandbox_config(path: Path) -> SandboxConfig:
    """Read + parse the config; a missing file yields safe defaults."""
    if not path.is_file():
        return SandboxConfig()
    return parse_sandbox_config(path.read_text(encoding="utf-8"))


def _parse_tier(data: dict[str, Any]) -> SandboxTier:
    raw = data.get("tier", SandboxTier.REPO_WRITE.wire)
    if not isinstance(raw, str):
        raise SandboxConfigError(f"'tier' must be a string, got {type(raw).__name__}")
    try:
        tier = SandboxTier.from_wire(raw)
    except ValueError as exc:
        raise SandboxConfigError(str(exc)) from exc
    if tier is SandboxTier.UNRESTRICTED and data.get("confirm_unrestricted") is not True:
        raise SandboxConfigError(
            "tier='unrestricted' requires confirm_unrestricted=true "
            "(it disables the write boundary and network gating)"
        )
    return tier


def _string_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise SandboxConfigError(f"'{key}' must be a list of non-empty strings")
    return tuple(raw)
