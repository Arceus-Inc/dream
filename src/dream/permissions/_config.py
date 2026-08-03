"""Operator sandbox posture config — ``.harness/sandbox.toml`` (Spec 13B).

Loads the session tier, repo-write escape-hatch roots, and add-only credential
patterns into a :class:`SandboxConfig`. The ``unrestricted`` tier is doubly
gated: ``tier = "unrestricted"`` also requires ``confirm_unrestricted = true``,
else the load fails — disabling the write boundary and network gating must be a
deliberate, reviewable act. A missing file yields safe defaults (``repo-write``);
a malformed or unsafe file fails fast.

``backend`` defaults to ``"docker"`` (container execution). Set
``backend = "subprocess"`` to opt out. Docker-specific knobs live under
``[docker]``. Backend is never inferred from the permission tier alone.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dream.permissions._types import SandboxTier
from dream.sandbox.docker_backend import DockerSandboxConfig
from dream.sandbox.docker_image import DEFAULT_IMAGE


class SandboxConfigError(ValueError):
    """Raised when ``sandbox.toml`` is malformed or unsafely configured."""


_VALID_BACKENDS = frozenset({"subprocess", "docker"})


@dataclass(frozen=True)
class SandboxConfig:
    """The operator's sandbox posture, parsed from ``sandbox.toml``."""

    tier: SandboxTier = SandboxTier.REPO_WRITE
    extra_allowed: tuple[str, ...] = ()
    credential_extra: tuple[str, ...] = ()
    backend: str = "docker"
    docker: DockerSandboxConfig = field(default_factory=DockerSandboxConfig)


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
        backend=_parse_backend(data),
        docker=_parse_docker(data),
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


def _parse_backend(data: dict[str, Any]) -> str:
    raw = data.get("backend", "docker")
    if not isinstance(raw, str):
        raise SandboxConfigError(f"'backend' must be a string, got {type(raw).__name__}")
    if raw not in _VALID_BACKENDS:
        raise SandboxConfigError(
            f"unknown sandbox backend {raw!r}; expected one of "
            f"{sorted(_VALID_BACKENDS)}"
        )
    return raw


def _parse_docker(data: dict[str, Any]) -> DockerSandboxConfig:
    raw = data.get("docker", {})
    if raw is None:
        return DockerSandboxConfig()
    if not isinstance(raw, dict):
        raise SandboxConfigError("'docker' must be a table")

    image = raw.get("image", DEFAULT_IMAGE)
    if not isinstance(image, str) or not image:
        raise SandboxConfigError("'docker.image' must be a non-empty string")

    auto_build = raw.get("auto_build_image", True)
    if not isinstance(auto_build, bool):
        raise SandboxConfigError("'docker.auto_build_image' must be a boolean")

    cpu_limit = raw.get("cpu_limit", 0.0)
    if isinstance(cpu_limit, int):
        cpu_limit = float(cpu_limit)
    if not isinstance(cpu_limit, float):
        raise SandboxConfigError("'docker.cpu_limit' must be a number")

    memory_limit = raw.get("memory_limit", "")
    if not isinstance(memory_limit, str):
        raise SandboxConfigError("'docker.memory_limit' must be a string")

    extra_mounts = raw.get("extra_mounts", [])
    if not isinstance(extra_mounts, list) or not all(
        isinstance(item, str) and item for item in extra_mounts
    ):
        raise SandboxConfigError("'docker.extra_mounts' must be a list of non-empty strings")

    extra_env_raw = raw.get("extra_env", {})
    if not isinstance(extra_env_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in extra_env_raw.items()
    ):
        raise SandboxConfigError("'docker.extra_env' must be a string-to-string table")

    fail_if_unavailable = raw.get("fail_if_unavailable", True)
    if not isinstance(fail_if_unavailable, bool):
        raise SandboxConfigError("'docker.fail_if_unavailable' must be a boolean")

    return DockerSandboxConfig(
        image=image,
        auto_build_image=auto_build,
        cpu_limit=cpu_limit,
        memory_limit=memory_limit,
        extra_mounts=tuple(extra_mounts),
        extra_env=dict(extra_env_raw),
        fail_if_unavailable=fail_if_unavailable,
    )


def _string_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise SandboxConfigError(f"'{key}' must be a list of non-empty strings")
    return tuple(raw)
