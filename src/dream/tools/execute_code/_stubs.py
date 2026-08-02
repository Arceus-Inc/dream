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
        "command: str, timeout_seconds: float = 120.0",
        '"""Run a shell command. Returns the tool content string."""',
        '{"command": command, "timeout_seconds": timeout_seconds}',
    ),
    _StubSpec(
        NestedToolName.WEB_SEARCH,
        "query: str, max_results: int = 5",
        '"""Search the web. Returns the tool content string."""',
        '{"query": query, "max_results": max_results}',
    ),
    _StubSpec(
        NestedToolName.WEB_EXTRACT,
        "urls: list[str]",
        '"""Extract page content. Returns the tool content string."""',
        '{"urls": urls}',
    ),
)

_STUB_BY_NAME: dict[NestedToolName, _StubSpec] = {s.name: s for s in _STUBS}

_UDS_HEADER = '''\
"""Auto-generated Dream tools RPC stubs for execute_code."""
from __future__ import annotations

import json
import os
import socket
import threading

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


def _call(tool_name: str, args: dict) -> str:
    """Send a nested tool call to the parent and return content (or raise)."""
    request = json.dumps({
        "tool": tool_name,
        "args": args,
        "token": os.environ.get("DREAM_RPC_TOKEN", ""),
    }) + "\\n"
    with _call_lock:
        conn = _connect()
        conn.sendall(request.encode("utf-8"))
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("Dream agent process disconnected")
            buf += chunk
            if buf.endswith(b"\\n"):
                break
    payload = json.loads(buf.decode("utf-8").strip())
    # Transport / auth failures have no content — raise. Tool-level errors
    # (missing file, etc.) return content so scripts can branch (Hermes PTC).
    err = payload.get("error")
    content = payload.get("content") or ""
    if err and not content:
        raise RuntimeError(str(err))
    return content

'''


def generate_dream_tools_module(enabled: frozenset[NestedToolName]) -> str:
    """Build ``dream_tools.py`` source for the given enabled sandbox tools."""
    parts: list[str] = [_UDS_HEADER]
    export_names: list[str] = []
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


__all__ = ["generate_dream_tools_module"]
