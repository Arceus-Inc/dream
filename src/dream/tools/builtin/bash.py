"""Default ``bash`` tool -- non-PTY shell execution via asyncio.

Spec 05 slice B. Shape borrowed from OpenHarness ``bash_tool.py`` with two
deliberate deviations:

* No PTY (a sandbox concern, not a tool concern in dream).
* subprocess is spawned via ``asyncio.create_subprocess_exec`` -- the
  ``import subprocess`` invariant only restricts the stdlib module, not the
  asyncio API. The bash tool routes through a small interactive-scaffold
  preflight so installer prompts surface as a structured error rather than
  silently hanging until timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

_OUTPUT_CAP = 12_000
_READ_REMAINING_TIMEOUT = 2.0

# Heuristic -- commands that start with one of these tokens are treated as
# read-only for per-call tier gating. Matches the head whitespace-separated
# token only; we deliberately do NOT try to parse the full command line.
_READ_ONLY_HEADS: frozenset[str] = frozenset(
    {
        "ls",
        "dir",
        "pwd",
        "cd",
        "echo",
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "wc",
        "stat",
        "file",
        "find",
        "tree",
        "which",
        "where",
        "type",
        "grep",
        "rg",
        "ripgrep",
        "egrep",
        "fgrep",
        "git",
        "python",
        "python3",
        "node",
        "ruby",
        "perl",
        "uname",
        "hostname",
        "whoami",
        "id",
        "env",
        "printenv",
        "date",
    }
)


class BashInput(BaseModel):
    """Arguments for the ``bash`` tool."""

    command: str = Field(description="Shell command line to execute.")
    cwd: str | None = Field(default=None, description="Override working directory.")
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)


class BashTool(BaseTool):
    """Execute a shell command with stdout/stderr capture."""

    name = "bash"
    description = "Run a shell command in the local repository."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=600.0)
    input_model = BashInput

    def is_read_only_for(self, input: dict[str, Any]) -> bool:
        command = str(input.get("command", "")).strip()
        if not command:
            return False
        try:
            head = shlex.split(command)[0]
        except ValueError:
            return False
        return Path(head).name.lower() in _READ_ONLY_HEADS

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = BashInput.model_validate(input)
        cwd = Path(args.cwd).expanduser() if args.cwd else ctx.working_dir

        if _looks_like_interactive_scaffold(args.command):
            return ToolResult(
                content=(
                    "Command appears to require interactive input; bash is "
                    "non-interactive. Rerun with non-interactive flags "
                    "(e.g. --yes, -y, --skip-install)."
                ),
                is_error=True,
                metadata={
                    "command": args.command,
                    "interactive_required": True,
                    "returncode": None,
                    "timed_out": False,
                    "root_cause": "interactive prompt required",
                    "safe_retry": "rerun with non-interactive flags",
                    "stop_condition": "do not retry the same interactive command",
                },
            )

        argv = _shell_argv(args.command)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            await asyncio.wait_for(process.wait(), timeout=args.timeout_seconds)
        except TimeoutError:
            partial = await _drain(process.stdout)
            await _kill(process)
            partial.extend(await _read_remaining(process))
            return ToolResult(
                content=_format_timeout(partial, args.command, args.timeout_seconds),
                is_error=True,
                metadata={
                    "command": args.command,
                    "returncode": process.returncode,
                    "timed_out": True,
                    "root_cause": f"command timed out after {args.timeout_seconds}s",
                    "safe_retry": "rerun with a tighter scope or larger timeout",
                    "stop_condition": "do not retry beyond the declared tool timeout",
                },
            )

        buffer = await _read_remaining(process)
        text = _format(buffer)
        is_error = process.returncode != 0
        metadata: dict[str, Any] = {
            "command": args.command,
            "returncode": process.returncode,
            "timed_out": False,
        }
        if is_error:
            metadata.update(
                {
                    "root_cause": f"exit code {process.returncode}",
                    "safe_retry": "inspect output, adjust arguments, and rerun",
                    "stop_condition": "do not retry on the same arguments after two failures",
                }
            )
        return ToolResult(content=text, is_error=is_error, metadata=metadata)


def _shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        return ["cmd.exe", "/c", command]
    return ["/bin/sh", "-c", command]


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=2.0)


async def _drain(stream: asyncio.StreamReader | None, *, read_timeout: float = 0.05) -> bytearray:
    out = bytearray()
    if stream is None:
        return out
    while True:
        try:
            chunk = await asyncio.wait_for(stream.read(65536), timeout=read_timeout)
        except TimeoutError:
            return out
        if not chunk:
            return out
        out.extend(chunk)


async def _read_remaining(process: asyncio.subprocess.Process) -> bytearray:
    out = bytearray()
    if process.stdout is None:
        return out
    try:
        remaining = await asyncio.wait_for(process.stdout.read(), timeout=_READ_REMAINING_TIMEOUT)
    except TimeoutError:
        remaining = b""
    out.extend(remaining)
    return out


def _format(buffer: bytearray) -> str:
    text = buffer.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    if not text:
        return "(no output)"
    if len(text) > _OUTPUT_CAP:
        return f"{text[:_OUTPUT_CAP]}\n...[truncated]..."
    return text


def _format_timeout(buffer: bytearray, command: str, timeout: float) -> str:
    parts = [f"Command timed out after {timeout} seconds."]
    text = _format(buffer)
    if text != "(no output)":
        parts.extend(["", "Partial output:", text])
    if _looks_like_interactive_scaffold(command):
        parts.extend(
            [
                "",
                "Command looks interactive; rerun with non-interactive flags.",
            ]
        )
    return "\n".join(parts)


def _looks_like_interactive_scaffold(command: str) -> bool:
    lowered = command.lower()
    scaffold = (
        "create-next-app",
        "npm create ",
        "pnpm create ",
        "yarn create ",
        "bun create ",
        "npm init ",
        "pnpm init ",
        "yarn init ",
        "npx create-",
        "bunx create-",
    )
    non_interactive = ("--yes", " -y", "--skip-install", "--defaults", "--non-interactive", "--ci")
    return any(m in lowered for m in scaffold) and not any(m in lowered for m in non_interactive)


__all__ = ["BashInput", "BashTool"]
