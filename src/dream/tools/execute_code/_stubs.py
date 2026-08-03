"""Generate the child ``dream_tools`` RPC stub module (Hermes PTC pattern)."""

from __future__ import annotations

from dataclasses import dataclass

from dream.tools.execute_code._types import NestedToolName


@dataclass(frozen=True, slots=True)
class _StubSpec:
    """Typed stub template — no stringly ``dict[str, tuple]`` tables at call sites."""

    name: NestedToolName
    signature: str
    docstring: str
    args_expr: str


_STUBS: tuple[_StubSpec, ...] = (
    _StubSpec(
        NestedToolName.READ_FILE,
        "path: str, offset: int = 0, limit: int = 2000",
        '"""Read a text file. Returns the tool content string (line-numbered)."""',
        '{"path": path, "offset": offset, "limit": limit}',
    ),
    _StubSpec(
        NestedToolName.WRITE_FILE,
        "path: str, content: str",
        '"""Create or overwrite a text file. Returns the tool content string."""',
        '{"path": path, "content": content}',
    ),
    _StubSpec(
        NestedToolName.EDIT_FILE,
        "path: str, old_str: str, new_str: str, replace_all: bool = False",
        '"""Replace a substring in an existing file. Returns the tool content string."""',
        '{"path": path, "old_str": old_str, "new_str": new_str, "replace_all": replace_all}',
    ),
    _StubSpec(
        NestedToolName.GREP,
        "pattern: str, path: str | None = None, limit: int = 100",
        '"""Regex search file contents. Returns the tool content string."""',
        '{"pattern": pattern, "path": path, "limit": limit}',
    ),
    _StubSpec(
        NestedToolName.GLOB,
        "pattern: str, path: str | None = None, limit: int = 200",
        '"""List files matching a glob. Returns the tool content string."""',
        '{"pattern": pattern, "path": path, "limit": limit}',
    ),
    _StubSpec(
        NestedToolName.BASH,
        "command: str, cwd: str | None = None, timeout_seconds: float = 120.0",
        '"""Run a shell command (optional cwd within the working directory)."""',
        '{"command": command, "cwd": cwd, "timeout_seconds": timeout_seconds}',
    ),
    _StubSpec(
        NestedToolName.WEB_SEARCH,
        "query: str, max_results: int = 5",
        '"""Search the web. Returns the tool content string."""',
        '{"query": query, "max_results": max_results}',
    ),
    _StubSpec(
        NestedToolName.WEB_EXTRACT,
        (
            "urls: list[str], extract_depth: str = 'basic', "
            "format: str = 'markdown', include_images: bool = False"
        ),
        '"""Extract page content (depth/format/images match direct web_extract)."""',
        (
            '{"urls": urls, "extract_depth": extract_depth, '
            '"format": format, "include_images": include_images}'
        ),
    ),
)

_STUB_BY_NAME: dict[NestedToolName, _StubSpec] = {s.name: s for s in _STUBS}

_UDS_HEADER = r'''
"""Auto-generated Dream tools RPC stubs for execute_code."""
from __future__ import annotations

import json
import os
import shlex
import socket
import threading
import time

_sock = None
_call_lock = threading.Lock()


def _connect():
    global _sock
    if _sock is None:
        endpoint = os.environ["DREAM_RPC_SOCKET"]
        if endpoint.startswith("tcp://"):
            host_port = endpoint[len("tcp://"):]
            host, _, port = host_port.rpartition(":")
            _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _sock.connect((host or "127.0.0.1", int(port)))
        else:
            _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _sock.connect(endpoint)
        _sock.settimeout(300)
    return _sock


def call_tool(tool_name: str, **kwargs) -> dict:
    """Send a nested tool call; return ``{"content", "is_error"}``."""
    request = json.dumps({
        "tool": tool_name,
        "args": kwargs,
        "token": os.environ.get("DREAM_RPC_TOKEN", ""),
    }) + "\n"
    with _call_lock:
        conn = _connect()
        conn.sendall(request.encode("utf-8"))
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("Dream agent process disconnected")
            buf += chunk
            if buf.endswith(b"\n"):
                break
    payload = json.loads(buf.decode("utf-8").strip())
    err = payload.get("error")
    content = payload.get("content") or ""
    is_error = bool(payload.get("is_error")) or (bool(err) and not content)
    if err and not content:
        raise RuntimeError(str(err))
    return {"content": content, "is_error": is_error, "error": err}


def _call(tool_name: str, args: dict) -> str:
    """Send a nested tool call to the parent and return content (or raise)."""
    result = call_tool(tool_name, **args)
    return result["content"]


def json_parse(s: str):
    """Parse JSON text; convenience for scripts."""
    return json.loads(s)


def shell_quote(s: str) -> str:
    """Shell-escape a string for safe interpolation into bash commands."""
    return shlex.quote(s)


def retry(fn, times: int = 3, delay: float = 0.5):
    """Call ``fn`` up to ``times``, sleeping ``delay`` between failures."""
    last = None
    for attempt in range(max(1, times)):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — script helper
            last = exc
            if attempt + 1 >= times:
                break
            time.sleep(delay)
    raise last

'''.lstrip("\n")


def describe_allowed_tools(enabled: frozenset[NestedToolName]) -> str:
    """Human-readable signature list for the tool description."""
    lines: list[str] = []
    for name in sorted(enabled, key=lambda n: n.value):
        spec = _STUB_BY_NAME.get(name)
        if spec is None:
            continue
        lines.append(f"- {spec.name.value}({spec.signature})")
    return "\n".join(lines)


def generate_dream_tools_module(enabled: frozenset[NestedToolName]) -> str:
    """Build ``dream_tools.py`` source for the given enabled sandbox tools."""
    parts: list[str] = [_UDS_HEADER]
    export_names: list[str] = [
        "call_tool",
        "json_parse",
        "shell_quote",
        "retry",
    ]
    for name in sorted(enabled, key=lambda n: n.value):
        spec = _STUB_BY_NAME.get(name)
        if spec is None:
            continue
        parts.append(
            f"def {spec.name.value}({spec.signature}):\n"
            f"    {spec.docstring}\n"
            f"    return _call({spec.name.value!r}, {spec.args_expr})\n"
        )
        export_names.append(spec.name.value)
    parts.append(f"\n__all__ = {export_names!r}\n")
    return "\n".join(parts)


__all__ = ["describe_allowed_tools", "generate_dream_tools_module"]
