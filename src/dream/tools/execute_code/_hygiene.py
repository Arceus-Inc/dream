"""Stdout/stderr hygiene before parent ToolResult (ANSI strip + secret redaction)."""

from __future__ import annotations

import re

__all__ = ["redact_secrets", "sanitize_output", "strip_ansi"]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b[PX^_].*?\x1b\\")

_ENV_SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|DSN|WEBHOOK|BEARER|APIKEY)[A-Z0-9_]*)\s*[=:]\s*([^\s'\"#,;]+)"
)

_OPAQUE_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(sk|rk|pk|ak)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from ``text``."""
    return _ANSI_RE.sub("", text)


def redact_secrets(text: str) -> str:
    """Redact common credential shapes from tool output."""

    def _env_repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}=***REDACTED***"

    out = _ENV_SECRET_RE.sub(_env_repl, text)
    for pattern in _OPAQUE_SECRET_PATTERNS:
        out = pattern.sub("***REDACTED***", out)
    return out


def sanitize_output(text: str) -> str:
    """Strip ANSI then redact secrets."""
    return redact_secrets(strip_ansi(text))
