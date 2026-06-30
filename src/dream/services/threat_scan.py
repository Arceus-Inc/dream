"""Lurkr-class threat scan at session start (Spec 13E).

Three blocking categories over the worktree, run before orientation; any
finding aborts the session (``has_blocking``). Reuses the spec-01
:class:`~dream.services.repo_validator.Finding` so the session-start gate can
treat structural and security findings uniformly.

- ``secret`` — secret-shaped strings in any text file the agent could read
  (worktree-wide, minus noise dirs), the matched value always redacted.
- ``world_writable`` — a world-writable file under ``docs/``.
- ``eval_in_tool`` — ``eval``/``exec``/``subprocess`` used by an operator tool
  under ``.harness/tools/`` (AST-based, so strings/comments never match). The
  harness's own built-ins under ``src/`` are never scanned.

Operators suppress known false positives via ``.harness/lurkr-ignore.toml``
(path globs and/or category codes). The two deferred categories
(``unverified_mcp``, ``prompt_interpolation``) are leftover spec #04.
"""

from __future__ import annotations

import ast
import os
import re
import stat
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.permissions._globs import glob_to_regex
from dream.services.repo_validator import Finding

__all__ = ["LurkrIgnore", "LurkrIgnoreError", "load_lurkr_ignore", "threat_scan"]

_NOISE_DIRS = frozenset(
    {
        ".git",
        ".dream",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
_TEXT_SUFFIXES = {
    ".md",
    ".json",
    ".jsonc",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".env",
    ".sh",
    ".bash",
    ".zsh",
    # Credential-bearing formats — the whole point of the secret gate.
    ".pem",
    ".key",
    ".crt",
    ".cert",
    ".pub",
    ".pfx",
    ".p12",
    ".ovpn",
    ".conf",
    ".properties",
    ".xml",
    ".tf",
    ".tfvars",
    ".rb",
    ".go",
    ".java",
    ".php",
    ".pl",
    # Additional source file extensions — secrets leak into any language.
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".swift",
    ".kt",
    ".kts",
    ".scala",
    ".r",
    ".lua",
    ".ex",
    ".exs",
    ".vue",
    ".svelte",
    ".gradle",
    ".cmake",
    ".dockerfile",
}
# Extensionless files that commonly hold credentials. Matched by exact
# (lower-cased) name so the scanner doesn't have to sniff every binary blob.
_CREDENTIAL_FILENAMES = frozenset(
    {
        "credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".netrc",
        ".pgpass",
        ".htpasswd",
        ".npmrc",
        ".pypirc",
        ".dockercfg",
        ".gitcredentials",
    }
)
_MAX_FILE_BYTES = 1_000_000

# Secret-shaped patterns. The matched value is NEVER placed in a finding.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"gh[pous]_[A-Za-z0-9]{36}")),
    ("slack_token", re.compile(r"xox[bpras]-[A-Za-z0-9-]{10,}")),
    ("stripe_secret_key", re.compile(r"[sr]k_live_[A-Za-z0-9]{20,}")),
    ("google_api_key", re.compile(r"AIza[A-Za-z0-9_-]{35}")),
    ("heroku_api_key", re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")),
    ("azure_storage_key", re.compile(r"DefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[^;]+")),
)


class LurkrIgnoreError(ValueError):
    """Raised when ``.harness/lurkr-ignore.toml`` is malformed."""


@dataclass(frozen=True)
class LurkrIgnore:
    """Operator suppression: path globs and/or category codes."""

    paths: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()

    def suppresses(self, code: str, path: str | None) -> bool:
        if code in self.codes:
            return True
        if path is None:
            return False
        # Glob patterns are POSIX ("/"-separated); normalize the candidate so a
        # Windows-style backslash path still matches "tests/**"-shaped globs.
        posix_path = path.replace("\\", "/")
        return any(
            glob_to_regex(glob).fullmatch(posix_path) is not None for glob in self.paths
        )


def load_lurkr_ignore(paths: DreamPaths) -> LurkrIgnore:
    """Read ``.harness/lurkr-ignore.toml``; a missing file yields no suppressions."""
    config = paths.repo / ".harness" / "lurkr-ignore.toml"
    if not config.is_file():
        return LurkrIgnore()
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LurkrIgnoreError(f"invalid lurkr-ignore TOML: {exc}") from exc
    return LurkrIgnore(
        paths=_string_list(data, "paths"),
        codes=_string_list(data, "codes"),
    )


def threat_scan(paths: DreamPaths) -> list[Finding]:
    """Run the three session-start threat checks, minus operator suppressions.

    A malformed ``.harness/lurkr-ignore.toml`` is surfaced as a blocking
    finding rather than raised, so the session-start gate fails closed without
    crashing startup.
    """
    try:
        ignore = load_lurkr_ignore(paths)
    except LurkrIgnoreError as exc:
        return [
            Finding(
                "blocking",
                "lurkr_ignore_invalid",
                f"invalid .harness/lurkr-ignore.toml: {exc}",
                ".harness/lurkr-ignore.toml",
            )
        ]
    raw: list[Finding] = []
    raw += _scan_secrets(paths)
    raw += _scan_world_writable(paths)
    raw += _scan_eval_in_tool(paths)
    return [f for f in raw if not ignore.suppresses(f.code, f.path)]


def _scan_secrets(paths: DreamPaths) -> list[Finding]:
    findings: list[Finding] = []
    for path in _walk_text_files(paths.repo):
        rel = path.relative_to(paths.repo).as_posix()
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(
                    Finding("blocking", "secret", f"possible {name} found (value redacted)", rel)
                )
    return findings


def _scan_world_writable(paths: DreamPaths) -> list[Finding]:
    findings: list[Finding] = []
    docs = paths.docs_dir
    if not docs.is_dir():
        return findings
    for path in sorted(docs.rglob("*")):
        if not path.is_file():
            continue
        try:
            mode = path.stat().st_mode
        except OSError:
            continue
        if mode & stat.S_IWOTH:
            rel = path.relative_to(paths.repo).as_posix()
            findings.append(Finding("blocking", "world_writable", "file is world-writable", rel))
    return findings


def _scan_eval_in_tool(paths: DreamPaths) -> list[Finding]:
    findings: list[Finding] = []
    tools_dir = paths.repo / ".harness" / "tools"
    if not tools_dir.is_dir():
        return findings
    for path in sorted(tools_dir.rglob("*.py")):
        rel = path.relative_to(paths.repo).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            # An unparseable file can't be imported as a tool either — not a
            # live threat, so skip rather than block on a work-in-progress file.
            continue
        if _uses_dangerous(tree):
            findings.append(
                Finding("blocking", "eval_in_tool", "tool uses eval/exec/subprocess", rel)
            )
    return findings


def _is_eval_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"eval", "exec"}
    )


def _imports_subprocess(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "subprocess" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "subprocess"
    return False


def _calls_subprocess_attr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
    )


def _uses_dangerous(tree: ast.AST) -> bool:
    return any(
        _is_eval_call(node)
        or _imports_subprocess(node)
        or _calls_subprocess_attr(node)
        for node in ast.walk(tree)
    )


def _walk_text_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _NOISE_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if (
                path.suffix.lower() in _TEXT_SUFFIXES
                or name.startswith(".env")
                or name.lower() in _CREDENTIAL_FILENAMES
            ):
                yield path


def _string_list(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise LurkrIgnoreError(f"'{key}' must be a list of non-empty strings")
    return tuple(raw)
