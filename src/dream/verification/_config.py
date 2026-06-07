"""Parse ``.harness/verification.toml`` into verification steps (Spec 12c).

Until the #10 sprint contract supplies ``verification_steps`` directly, the
operator declares them here (same ``.harness/`` home as the MCP allowlist):

    [[step]]
    name = "unit tests"
    command = "pytest -q"

A missing file means no steps (empty report), not an error; a malformed file
fails fast.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from dream.verification._types import VerificationStepSpec


class VerificationConfigError(ValueError):
    """Raised when ``verification.toml`` is malformed."""


def parse_verification_config(text: str) -> list[VerificationStepSpec]:
    """Parse the config body into declared steps."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise VerificationConfigError(f"invalid verification TOML: {exc}") from exc

    raw = data.get("step", [])
    if not isinstance(raw, list):
        raise VerificationConfigError("verification '[[step]]' must be an array of tables")
    return [_step_from_table(item) for item in raw]


def read_verification_config(path: Path) -> list[VerificationStepSpec]:
    """Read + parse the config; a missing file yields no steps."""
    if not path.is_file():
        return []
    return parse_verification_config(path.read_text(encoding="utf-8"))


def _step_from_table(item: Any) -> VerificationStepSpec:
    if not isinstance(item, dict):
        raise VerificationConfigError(
            f"each '[[step]]' must be a table, got {type(item).__name__}"
        )
    command = item.get("command")
    if not (isinstance(command, str) and command.strip()):
        raise VerificationConfigError(f"verification step missing 'command': {item!r}")
    name = item.get("name", "")
    if not isinstance(name, str):
        raise VerificationConfigError(f"verification step 'name' must be a string: {item!r}")
    return VerificationStepSpec(command=command, name=name)


__all__ = [
    "VerificationConfigError",
    "parse_verification_config",
    "read_verification_config",
]
