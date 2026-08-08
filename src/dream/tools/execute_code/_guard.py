"""Pre-spawn script guard for execute_code (Hermes-inspired).

Nested ``bash`` / ``web_*`` via ``dream_tools`` is the intended shell/network
path. Raw process spawn and outbound network APIs in the child script are
refused so they cannot bypass the allowlist + permission gate.
"""

from __future__ import annotations

import ast
import re

__all__ = ["check_execute_code_guard"]

_BLOCKED_IMPORT_ROOTS = frozenset(
    {
        "subprocess",
        "ctypes",
        "multiprocessing",
        "pty",
        "asyncio.subprocess",
    }
)

_BLOCKED_ATTR_CALLS = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "execv"),
        ("os", "execve"),
        ("os", "execl"),
        ("os", "execlp"),
        ("os", "execvp"),
        ("os", "execvpe"),
        ("os", "spawnv"),
        ("os", "spawnve"),
        ("os", "spawnvp"),
        ("os", "spawnvpe"),
        ("os", "spawnl"),
        ("os", "spawnle"),
        ("os", "spawnlp"),
        ("os", "spawnlpe"),
    }
)

_BLOCKED_NAME_CALLS = frozenset({"system", "popen"})

_NETWORK_MODULE_ROOTS = frozenset(
    {
        "socket",
        "http",
        "urllib",
        "aiohttp",
        "requests",
        "httpx",
    }
)

# Fallback regex when AST parse fails (syntax errors still go to the child).
_REGEX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsubprocess\b"), "subprocess"),
    (re.compile(r"\bos\.system\b"), "os.system"),
    (re.compile(r"\bos\.popen\b"), "os.popen"),
    (re.compile(r"\bos\.spawn"), "os.spawn*"),
    (re.compile(r"\bos\.exec"), "os.exec*"),
    (re.compile(r"\bctypes\b"), "ctypes"),
    (re.compile(r"\bmultiprocessing\b"), "multiprocessing"),
    (re.compile(r"\bpty\."), "pty"),
    (re.compile(r"\bsocket\.socket\b"), "socket.socket"),
    (re.compile(r"\burllib\.request\b"), "urllib.request"),
    (re.compile(r"\bhttp\.client\b"), "http.client"),
    (re.compile(r"\baiohttp\b"), "aiohttp"),
    (re.compile(r"\brequests\."), "requests"),
    (re.compile(r"\bhttpx\b"), "httpx"),
)


def check_execute_code_guard(code: str) -> str | None:
    """Return a refusal message if ``code`` bypasses nested tools, else ``None``."""
    if not code or not code.strip():
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _regex_guard(code)

    for node in ast.walk(tree):
        hit = _check_node(node)
        if hit is not None:
            return _refuse(hit)
    return None


def _refuse(what: str) -> str:
    return (
        f"execute_code refused: blocked use of {what}. "
        "Use dream_tools.bash / web_search / web_fetch for shell and network I/O "
        "so nested calls stay on the allowlist + permission gate."
    )


def _check_node(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if alias.name in _BLOCKED_IMPORT_ROOTS or root in _BLOCKED_IMPORT_ROOTS:
                return alias.name
            if root in _NETWORK_MODULE_ROOTS:
                return alias.name
    if isinstance(node, ast.ImportFrom) and node.module:
        if node.module in _BLOCKED_IMPORT_ROOTS:
            return node.module
        root = node.module.split(".", 1)[0]
        if root in _BLOCKED_IMPORT_ROOTS or root in _NETWORK_MODULE_ROOTS:
            return node.module
    if isinstance(node, ast.Call):
        return _check_call(node)
    return None


def _check_call(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        pair = (func.value.id, func.attr)
        if pair in _BLOCKED_ATTR_CALLS:
            return f"{pair[0]}.{pair[1]}"
        if func.value.id in _NETWORK_MODULE_ROOTS:
            return f"{func.value.id}.{func.attr}"
        if func.value.id == "os" and func.attr.startswith(("spawn", "exec")):
            return f"os.{func.attr}"
    if isinstance(func, ast.Name):
        if func.id in _BLOCKED_NAME_CALLS:
            return func.id
        if func.id == "__import__" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                root = arg0.value.split(".", 1)[0]
                if (
                    arg0.value in _BLOCKED_IMPORT_ROOTS
                    or root in _BLOCKED_IMPORT_ROOTS
                    or root in _NETWORK_MODULE_ROOTS
                ):
                    return f"__import__({arg0.value!r})"
    return None


def _regex_guard(code: str) -> str | None:
    for pattern, label in _REGEX_PATTERNS:
        if pattern.search(code):
            return _refuse(label)
    return None
