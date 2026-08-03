"""Local UDS/TCP execute_code session — child script + parent RPC (Hermes PTC)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dream.tools.execute_code._hygiene import sanitize_output
from dream.tools.execute_code._invoker import ToolInvoker
from dream.tools.execute_code._observation import next_actions_for, summary_for
from dream.tools.execute_code._stubs import generate_dream_tools_module
from dream.tools.execute_code._types import (
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RPC_REQUEST_BYTES,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    ExecuteCodeOutcome,
    ExecuteCodeStatus,
    NestedToolName,
    RpcRequest,
    RpcResponse,
)

_IS_WINDOWS = platform.system() == "Windows"

_SAFE_ENV_PREFIXES: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SHELL",
    "LOGNAME",
    "XDG_",
    "VIRTUAL_ENV",
    "CONDA",
)
_SECRET_SUBSTRINGS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "PASSWD",
    "AUTH",
    "DSN",
    "WEBHOOK",
    "CREDS",
    "BEARER",
    "APIKEY",
)
_WINDOWS_ESSENTIAL: frozenset[str] = frozenset(
    {
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "PUBLIC",
        "ALLUSERSPROFILE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "USERDOMAIN",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "COMPUTERNAME",
    }
)


@dataclass(frozen=True, slots=True)
class _Truncation:
    text: str
    truncated: bool
    original_bytes: int


def _scrub_child_env(source: dict[str, str]) -> dict[str, str]:
    """Strip secrets from the child environment (Hermes scrub rules)."""
    cleaned: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if any(s in upper for s in _SECRET_SUBSTRINGS):
            continue
        if upper in _WINDOWS_ESSENTIAL:
            cleaned[key] = value
            continue
        if upper == "PYTHONPATH":
            continue
        if any(upper.startswith(p) or key.startswith(p) for p in _SAFE_ENV_PREFIXES):
            cleaned[key] = value
    return cleaned


def _truncate(text: str, max_bytes: int) -> _Truncation:
    """Keep ``max_bytes`` of UTF-8 output, reserving space for the truncation marker."""
    raw = text.encode("utf-8", errors="replace")
    original = len(raw)
    if original <= max_bytes:
        return _Truncation(text=text, truncated=False, original_bytes=original)
    omitted = original - max_bytes
    marker = f"\n\n... [OUTPUT TRUNCATED - {omitted:,} bytes omitted] ...\n\n"
    marker_b = marker.encode("utf-8")
    if len(marker_b) >= max_bytes:
        clipped = marker_b[:max_bytes].decode("utf-8", errors="replace")
        return _Truncation(text=clipped, truncated=True, original_bytes=original)
    budget = max_bytes - len(marker_b)
    head = budget // 2
    tail = budget - head
    clipped = (
        raw[:head].decode("utf-8", errors="replace")
        + marker
        + raw[-tail:].decode("utf-8", errors="replace")
    )
    return _Truncation(text=clipped, truncated=True, original_bytes=original)


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the process group (POSIX) so nested shells cannot outlive a timeout."""
    killpg = getattr(os, "killpg", None)
    if killpg is not None and proc.pid is not None and not _IS_WINDOWS:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            killpg(os.getpgid(proc.pid), signal.SIGKILL)
    else:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


def _outcome(
    *,
    status: ExecuteCodeStatus,
    output: str,
    exit_code: int,
    tool_calls_made: int,
    duration_seconds: float,
    stderr: str = "",
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    stdout_bytes: int = 0,
    stderr_bytes: int = 0,
    tool_call_log: list[dict[str, Any]] | None = None,
    detail: str = "",
) -> ExecuteCodeOutcome:
    return ExecuteCodeOutcome(
        status=status,
        output=output,
        exit_code=exit_code,
        tool_calls_made=tool_calls_made,
        duration_seconds=duration_seconds,
        stderr=stderr,
        summary=summary_for(
            status, exit_code=exit_code, tool_calls_made=tool_calls_made, detail=detail
        ),
        next_actions=next_actions_for(status),
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        tool_call_log=list(tool_call_log or ()),
    )


async def _communicate_with_cancel(
    proc: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None,
) -> tuple[bytes, bytes] | str:
    """Wait for process exit; return stdout/stderr bytes, or 'timeout'/'cancelled'.

    Uses a single ``communicate()`` task — calling it repeatedly after a
    timeout leaves pipes half-consumed and raises on the next attempt.
    """
    comm_task = asyncio.create_task(proc.communicate())
    deadline = time.monotonic() + timeout_seconds
    try:
        while not comm_task.done():
            if cancel_requested is not None and cancel_requested():
                await _kill_tree(proc)
                with contextlib.suppress(Exception):
                    await comm_task
                return "cancelled"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await _kill_tree(proc)
                with contextlib.suppress(Exception):
                    await comm_task
                return "timeout"
            await asyncio.wait({comm_task}, timeout=min(0.25, remaining))
        return await comm_task
    except BaseException:
        if not comm_task.done():
            await _kill_tree(proc)
            with contextlib.suppress(Exception):
                await comm_task
        raise


async def run_execute_code_session(
    *,
    code: str,
    working_dir: Path,
    allowed: frozenset[NestedToolName],
    invoker: ToolInvoker,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    cancel_requested: Callable[[], bool] | None = None,
) -> ExecuteCodeOutcome:
    """Spawn the child script and serve nested tool RPCs until exit/timeout/cancel."""
    del max_tool_calls  # enforced on the invoker; kept for API symmetry with Hermes
    if not allowed:
        msg = (
            "execute_code refused: no sandbox tools available "
            "(session ∩ allowlist is empty; fail-closed)."
        )
        return _outcome(
            status=ExecuteCodeStatus.REFUSED,
            output=msg,
            exit_code=1,
            tool_calls_made=0,
            duration_seconds=0.0,
            detail="empty allowlist",
        )

    started = time.monotonic()
    tmpdir = Path(tempfile.mkdtemp(prefix="dream_sandbox_"))
    sock_tmpdir = Path("/tmp") if sys.platform == "darwin" else Path(tempfile.gettempdir())
    use_tcp = _IS_WINDOWS
    sock_path: Path | None = None
    rpc_endpoint: str
    server_sock: socket.socket | None = None
    stop = asyncio.Event()

    try:
        (tmpdir / "dream_tools.py").write_text(
            generate_dream_tools_module(allowed),
            encoding="utf-8",
        )
        (tmpdir / "script.py").write_text(code, encoding="utf-8")

        rpc_token = secrets.token_urlsafe(32)
        if use_tcp:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind(("127.0.0.1", 0))
            host, port = server_sock.getsockname()[:2]
            rpc_endpoint = f"tcp://{host}:{port}"
        else:
            sock_path = sock_tmpdir / f"dream_rpc_{uuid.uuid4().hex}.sock"
            if sock_path.exists():
                sock_path.unlink()
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(str(sock_path))
            os.chmod(sock_path, 0o600)
            rpc_endpoint = str(sock_path)
        server_sock.listen(1)
        server_sock.setblocking(False)

        rpc_task = asyncio.create_task(
            _serve_rpc(server_sock, invoker, rpc_token, stop),
            name="dream-execute-code-rpc",
        )

        child_env = _scrub_child_env(dict(os.environ))
        child_env["DREAM_RPC_SOCKET"] = rpc_endpoint
        child_env["DREAM_RPC_TOKEN"] = rpc_token
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONPATH"] = str(tmpdir)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(tmpdir / "script.py"),
            cwd=str(working_dir),
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=not _IS_WINDOWS,
        )

        try:
            result = await _communicate_with_cancel(
                proc,
                timeout_seconds=timeout_seconds,
                cancel_requested=cancel_requested,
            )
        finally:
            stop.set()
            rpc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await rpc_task

        duration = time.monotonic() - started
        log = invoker.tool_call_log

        if result == "timeout":
            msg = f"execute_code timed out after {timeout_seconds}s"
            return _outcome(
                status=ExecuteCodeStatus.TIMEOUT,
                output=msg,
                exit_code=-1,
                tool_calls_made=invoker.calls_made,
                duration_seconds=duration,
                stderr=msg,
                tool_call_log=log,
                detail=msg,
            )
        if result == "cancelled":
            msg = "execute_code cancelled by caller"
            return _outcome(
                status=ExecuteCodeStatus.CANCELLED,
                output=msg,
                exit_code=-1,
                tool_calls_made=invoker.calls_made,
                duration_seconds=duration,
                stderr=msg,
                tool_call_log=log,
                detail=msg,
            )

        if not isinstance(result, tuple):
            msg = f"execute_code ended unexpectedly: {result!r}"
            return _outcome(
                status=ExecuteCodeStatus.ERROR,
                output=msg,
                exit_code=-1,
                tool_calls_made=invoker.calls_made,
                duration_seconds=duration,
                stderr=msg,
                tool_call_log=log,
                detail=msg,
            )

        stdout_b, stderr_b = result
        stdout_raw = sanitize_output(stdout_b.decode("utf-8", errors="replace"))
        stderr_raw = sanitize_output(stderr_b.decode("utf-8", errors="replace"))
        stdout_t = _truncate(stdout_raw, MAX_STDOUT_BYTES)
        stderr_t = _truncate(stderr_raw, MAX_STDERR_BYTES)
        exit_code = proc.returncode if proc.returncode is not None else 1

        if invoker.cap_exceeded:
            status = ExecuteCodeStatus.CAP_EXCEEDED
        elif exit_code != 0:
            status = ExecuteCodeStatus.ERROR
        else:
            status = ExecuteCodeStatus.SUCCESS

        output = stdout_t.text
        if stderr_t.text and status is not ExecuteCodeStatus.SUCCESS:
            output = (
                f"{stdout_t.text}\n--- stderr ---\n{stderr_t.text}"
                if stdout_t.text
                else stderr_t.text
            )
        if not output and status is not ExecuteCodeStatus.SUCCESS:
            output = summary_for(
                status, exit_code=exit_code, tool_calls_made=invoker.calls_made
            )

        return _outcome(
            status=status,
            output=output,
            exit_code=exit_code,
            tool_calls_made=invoker.calls_made,
            duration_seconds=duration,
            stderr=stderr_t.text,
            stdout_truncated=stdout_t.truncated,
            stderr_truncated=stderr_t.truncated,
            stdout_bytes=stdout_t.original_bytes,
            stderr_bytes=stderr_t.original_bytes,
            tool_call_log=log,
        )
    finally:
        if server_sock is not None:
            server_sock.close()
        if sock_path is not None and sock_path.exists():
            sock_path.unlink(missing_ok=True)
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _serve_rpc(
    server_sock: socket.socket,
    invoker: ToolInvoker,
    rpc_token: str,
    stop: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    conn: socket.socket | None = None
    try:
        while not stop.is_set():
            try:
                conn, _ = await asyncio.wait_for(
                    loop.sock_accept(server_sock),
                    timeout=0.05,
                )
                break
            except TimeoutError:
                continue
        if conn is None:
            return
        conn.setblocking(False)

        buf = b""
        while not stop.is_set():
            try:
                chunk = await asyncio.wait_for(loop.sock_recv(conn, 65536), timeout=0.2)
            except TimeoutError:
                continue
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_RPC_REQUEST_BYTES and b"\n" not in buf:
                response = RpcResponse(
                    content="",
                    is_error=True,
                    error=(
                        f"RPC request exceeded {MAX_RPC_REQUEST_BYTES} bytes "
                        "without a terminating newline"
                    ),
                )
                payload = response.model_dump_json() + "\n"
                with contextlib.suppress(OSError):
                    await loop.sock_sendall(conn, payload.encode("utf-8"))
                break
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) > MAX_RPC_REQUEST_BYTES:
                    response = RpcResponse(
                        content="",
                        is_error=True,
                        error=f"RPC request line exceeded {MAX_RPC_REQUEST_BYTES} bytes",
                    )
                else:
                    line = line.strip()
                    if not line:
                        continue
                    response = await _dispatch_line(line, invoker, rpc_token)
                payload = response.model_dump_json() + "\n"
                await loop.sock_sendall(conn, payload.encode("utf-8"))
    finally:
        if conn is not None:
            conn.close()


async def _dispatch_line(
    line: bytes,
    invoker: ToolInvoker,
    rpc_token: str,
) -> RpcResponse:
    try:
        raw = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return RpcResponse(content="", is_error=True, error=f"Invalid RPC request: {exc}")

    if not isinstance(raw, dict):
        return RpcResponse(content="", is_error=True, error="Invalid RPC request: not an object")

    token = str(raw.get("token") or "")
    if not rpc_token or not secrets.compare_digest(token.encode(), rpc_token.encode()):
        return RpcResponse(content="", is_error=True, error="Unauthorized RPC request")

    try:
        request = RpcRequest.model_validate(raw)
    except ValidationError as exc:
        return RpcResponse(content="", is_error=True, error=f"Invalid RPC request: {exc}")

    try:
        result = await invoker.invoke(request.tool, request.args)
    except PermissionError as exc:
        return RpcResponse(content="", is_error=True, error=str(exc))
    except ValidationError as exc:
        return RpcResponse(content="", is_error=True, error=f"Invalid nested tool args: {exc}")
    except Exception as exc:
        return RpcResponse(
            content="",
            is_error=True,
            error=f"Nested tool {request.tool.value!r} failed: {type(exc).__name__}: {exc}",
        )

    if result.is_error:
        return RpcResponse(
            content=result.content,
            is_error=True,
            error=result.content,
        )
    return RpcResponse(content=result.content, is_error=False)


__all__ = ["DEFAULT_MAX_TOOL_CALLS", "DEFAULT_TIMEOUT_SECONDS", "run_execute_code_session"]
