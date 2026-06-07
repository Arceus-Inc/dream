"""Local MCP credentials — secrets kept OUT of the version-controlled allowlist.

The allowlist (``.harness/mcp-allowlist.toml``) is the VCS-tracked authority for
what *may* connect; it must never hold secrets. Credentials live in a sibling
``.harness/mcp-credentials.toml`` that is gitignored and operator-local. The
opener merges them into the connection config at connect time, so a freshly
written credential takes effect on the next (re)connect.

``mode`` records how to inject the secret:

- ``bearer`` — store ``"Bearer <value>"`` (the destination is chosen by transport).
- ``env`` / ``header`` — store ``value`` verbatim (the plain form per transport).

Where the secret lands is decided by the transport, not the mode: stdio servers
get it in ``env`` (default key ``MCP_AUTH_TOKEN``), http/ws servers in ``headers``
(default key ``Authorization``). Validating that a mode suits a transport is the
``mcp_auth`` tool's job; this module maps permissively so a connect never crashes
on a hand-edited file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dream.mcp._types import (
    McpServerConfig,
    McpStdioServerConfig,
)
from dream.utils.fs import atomic_write_text

CredentialMode = Literal["bearer", "header", "env"]

_VALID_MODES: tuple[CredentialMode, ...] = ("bearer", "header", "env")
_DEFAULT_ENV_KEY = "MCP_AUTH_TOKEN"
_DEFAULT_HEADER_KEY = "Authorization"
_OWNER_ONLY = 0o600


class CredentialsError(ValueError):
    """Raised when ``mcp-credentials.toml`` is malformed."""


@dataclass(frozen=True)
class ServerCredential:
    """One server's auth secret plus how to inject it."""

    mode: CredentialMode
    value: str
    key: str | None = None


def read_credentials(path: Path) -> dict[str, ServerCredential]:
    """Parse the gitignored credentials file; a missing file yields no creds."""
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CredentialsError(f"invalid credentials TOML: {exc}") from exc
    creds: dict[str, ServerCredential] = {}
    for name, raw in data.items():
        if not isinstance(raw, dict):
            raise CredentialsError(f"credentials entry {name!r} must be a table")
        creds[name] = _credential_from_table(name, raw)
    return creds


def write_credential(path: Path, server_name: str, credential: ServerCredential) -> None:
    """Merge one server's credential into the gitignored file (atomic, mode 0600)."""
    existing = read_credentials(path)
    existing[server_name] = credential
    atomic_write_text(path, _render(existing), mode=_OWNER_ONLY)


def apply_credentials(
    config: McpServerConfig, credential: ServerCredential | None
) -> McpServerConfig:
    """Return a NEW config with ``credential`` merged in (config unchanged)."""
    if credential is None:
        return config
    secret = f"Bearer {credential.value}" if credential.mode == "bearer" else credential.value
    if isinstance(config, McpStdioServerConfig):
        env = dict(config.env or {})
        env[credential.key or _DEFAULT_ENV_KEY] = secret
        return config.model_copy(update={"env": env})
    headers = dict(config.headers)
    headers[credential.key or _DEFAULT_HEADER_KEY] = secret
    return config.model_copy(update={"headers": headers})


# --- internals --------------------------------------------------------------


def _credential_from_table(name: str, raw: dict[str, object]) -> ServerCredential:
    mode = raw.get("mode")
    value = raw.get("value")
    key = raw.get("key")
    if mode not in _VALID_MODES:
        raise CredentialsError(
            f"credentials entry {name!r} has invalid mode {mode!r}; expected {_VALID_MODES}"
        )
    if not isinstance(value, str):
        raise CredentialsError(f"credentials entry {name!r} missing string 'value'")
    if key is not None and not isinstance(key, str):
        raise CredentialsError(f"credentials entry {name!r} 'key' must be a string")
    return ServerCredential(mode=mode, value=value, key=key)


def _render(creds: dict[str, ServerCredential]) -> str:
    lines: list[str] = []
    for name in sorted(creds):
        cred = creds[name]
        lines.append(f"[{_toml_basic_string(name)}]")
        lines.append(f"mode = {_toml_basic_string(cred.mode)}")
        lines.append(f"value = {_toml_basic_string(cred.value)}")
        if cred.key is not None:
            lines.append(f"key = {_toml_basic_string(cred.key)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _toml_basic_string(value: str) -> str:
    """Render ``value`` as a TOML basic string (also valid as a quoted key)."""
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            # TOML basic strings must escape C0 controls and DEL (U+007F).
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


__all__ = [
    "CredentialMode",
    "CredentialsError",
    "ServerCredential",
    "apply_credentials",
    "read_credentials",
    "write_credential",
]
