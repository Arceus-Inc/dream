"""Local UDS/TCP execute_code session — child script + parent RPC (Hermes PTC)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import secrets
import shutil
import socket
import sys
import tempfile
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from dream.tools.execute_code._invoker import ToolInvoker
from dream.tools.execute_code._stubs import generate_dream_tools_module
from dream.tools.execute_code._types import (
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_TIMEOUT_SECONDS,
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
    "PYTHONPATH",
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
    }
)


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
        if any(upper.startswith(p) or key.startswith(p) for p in _SAFE_ENV_PREFIXES):
            cleaned[key] = value
    return cleaned


def _truncate(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    head = int(max_bytes * 0.4)
    tail = max_bytes - head
    omitted = len(raw) - max_bytes
    return (
        raw[:head].decode("utf-8", errors="replace")
        + f"\n\n... [OUTPUT TRUNCATED - {omitted:,} bytes omitted] ...\n\n"
        + raw[-tail:].decode("utf-8", errors="replace")
    )


async def run_execute_code_session(
    *,
    code: str,
    working_dir: Path,
    allowed: frozenset[NestedToolName],
    invoker: ToolInvoker,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> ExecuteCodeOutcome:
    """Spawn the child script and serve nested tool RPCs until exit/timeout."""
    del max_tool_calls  # enforced on the invoker; kept for API symmetry with Hermes
    if not allowed:
        return ExecuteCodeOutcome(
            status=ExecuteCodeStatus.REFUSED,
            output=(
                "execute_code refused: no sandbox tools available "
                "(session ∩ allowlist is empty; fail-closed)."
            ),
            exit_code=1,
            tool_calls_made=0,
            duration_seconds=0.0,
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
        existing_pp = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = (
            f"{tmpdir}{os.pathsep}{existing_pp}" if existing_pp else str(tmpdir)
        )

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
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            stop.set()
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
                await proc.wait()
            duration = time.monotonic() - started
            return ExecuteCodeOutcome(
                status=ExecuteCodeStatus.TIMEOUT,
                output=_truncate("", MAX_STDOUT_BYTES),
                exit_code=-1,
                tool_calls_made=invoker.calls_made,
                duration_seconds=duration,
                stderr=_truncate(
                    f"execute_code timed out after {timeout_seconds}s",
                    MAX_STDERR_BYTES,
                ),
            )
        finally:
            stop.set()
            rpc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await rpc_task

        duration = time.monotonic() - started
        stdout = _truncate(stdout_b.decode("utf-8", errors="replace"), MAX_STDOUT_BYTES)
        stderr = _truncate(stderr_b.decode("utf-8", errors="replace"), MAX_STDERR_BYTES)
        exit_code = proc.returncode if proc.returncode is not None else 1

        if invoker.cap_exceeded:
            status = ExecuteCodeStatus.CAP_EXCEEDED
        elif exit_code != 0:
            status = ExecuteCodeStatus.ERROR
        else:
            status = ExecuteCodeStatus.SUCCESS

        output = stdout
        if stderr and status is not ExecuteCodeStatus.SUCCESS:
            output = f"{stdout}\n--- stderr ---\n{stderr}" if stdout else stderr

        return ExecuteCodeOutcome(
            status=status,
            output=output,
            exit_code=exit_code,
            tool_calls_made=invoker.calls_made,
            duration_seconds=duration,
            stderr=stderr,
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
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
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
        # Unknown / non-allowlisted tool names fail validation on NestedToolName.
        return RpcResponse(content="", is_error=True, error=f"Invalid RPC request: {exc}")

    try:
        result = await invoker.invoke(request.tool, request.args)
    except PermissionError as exc:
        return RpcResponse(content="", is_error=True, error=str(exc))

    if result.is_error:
        return RpcResponse(
            content=result.content,
            is_error=True,
            error=result.content,
        )
    return RpcResponse(content=result.content, is_error=False)


__all__ = ["run_execute_code_session"]
