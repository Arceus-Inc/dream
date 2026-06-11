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
from dream.sandbox import SandboxResult, read_sandbox_adapter
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within

_OUTPUT_CAP = 12_000
_READ_REMAINING_TIMEOUT = 2.0

# Heuristic -- commands that start with one of these tokens are treated as
# read-only for per-call tier gating. Matches the head whitespace-separated
# token only; we deliberately do NOT try to parse the full command line.
#
# NOTE: ``git`` is intentionally NOT in this set. ``git`` has both read-only
# and heavily mutating subcommands (commit, reset, clean, push), so a blanket
# head match would downclassify ``git reset --hard`` as "safe". git is gated
# separately via ``_GIT_READ_ONLY_SUBCOMMANDS`` below.
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
        "uname",
        "hostname",
        "whoami",
        "id",
        "env",
        "printenv",
        "date",
    }
)

# Vetted read-only git subcommands. Mirrors the always-read-only set of the
# dedicated GitTool. ``git <one of these>`` is read-only; everything else
# (commit, reset, clean, checkout, merge, rebase, push, branch <name>, ...)
# is NOT downclassified.
_GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "ls-files",
        "ls-tree",
        "describe",
        "blame",
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

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = BashInput.model_validate(input)
        return ToolEffects(command=args.command)

    def is_read_only_for(self, input: dict[str, Any]) -> bool:
        command = str(input.get("command", "")).strip()
        if not command:
            return False
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if not tokens:
            return False
        head = Path(tokens[0]).name.lower()
        if head == "git":
            # git is read-only only for a vetted subcommand allowlist; a bare
            # ``git`` or a mutating subcommand is NOT downclassified.
            return len(tokens) >= 2 and tokens[1] in _GIT_READ_ONLY_SUBCOMMANDS
        return head in _READ_ONLY_HEADS

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = BashInput.model_validate(input)
        # Confinement runs FIRST so a backend swap can never reopen the
        # worktree-escape hole: an out-of-tree cwd is refused before any
        # command reaches the adapter or our own spawn path.
        cwd = _resolve_cwd(args, ctx)
        if isinstance(cwd, ToolResult):
            return cwd
        if _looks_like_interactive_scaffold(args.command):
            return _interactive_required(args.command)
        # Spec 13B: when a session wired a SandboxAdapter, the backend is the
        # single execution mechanism — route the (already-confined) command
        # through it. A bare engine / older caller has no adapter, so fall
        # back to our own asyncio spawn so nothing breaks.
        adapter = read_sandbox_adapter(ctx.metadata)
        if adapter is not None:
            outcome = await adapter.run(
                args.command, cwd=cwd, timeout_seconds=args.timeout_seconds
            )
            return _result_from_sandbox(outcome, args.command, args.timeout_seconds)
        spawned = await _spawn(args.command, cwd)
        if isinstance(spawned, ToolResult):
            return spawned
        return await _run_with_timeout(spawned, args.command, args.timeout_seconds)


def _resolve_cwd(args: BashInput, ctx: ToolExecutionContext) -> Path | ToolResult:
    """Confine an operator/model-supplied cwd to the working directory.

    A relative cwd (``.``, ``sub``) is joined onto ``ctx.working_dir`` and an
    absolute or ``..`` path that escapes it is refused. Resolving against the
    *process* cwd let a worker pass ``cwd="."`` and operate on the host repo
    instead of its worktree (sandbox escape).
    """
    if not args.cwd:
        return ctx.working_dir
    try:
        return resolve_within(ctx.working_dir, args.cwd)
    except PathEscapesRoot as exc:
        return ToolResult(
            content=f"Path outside the working directory: {args.cwd}",
            is_error=True,
            metadata={
                "command": args.command,
                "returncode": None,
                "timed_out": False,
                "root_cause": str(exc),
                "safe_retry": "pass a cwd within the working directory, or omit it",
                "stop_condition": "do not retry with the same out-of-tree cwd",
            },
        )


def _interactive_required(command: str) -> ToolResult:
    return ToolResult(
        content=(
            "Command appears to require interactive input; bash is "
            "non-interactive. Rerun with non-interactive flags "
            "(e.g. --yes, -y, --skip-install)."
        ),
        is_error=True,
        metadata={
            "command": command,
            "interactive_required": True,
            "returncode": None,
            "timed_out": False,
            "root_cause": "interactive prompt required",
            "safe_retry": "rerun with non-interactive flags",
            "stop_condition": "do not retry the same interactive command",
        },
    )


async def _spawn(command: str, cwd: Path) -> asyncio.subprocess.Process | ToolResult:
    """Spawn the shell for ``command``; on spawn failure return a tool error."""
    try:
        return await asyncio.create_subprocess_exec(
            *_shell_argv(command),
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return ToolResult(
            content=f"failed to spawn shell: {exc}",
            is_error=True,
            metadata={
                "command": command,
                "returncode": None,
                "timed_out": False,
                "root_cause": f"shell spawn failed: {exc}",
                "safe_retry": "verify the working directory exists",
                "stop_condition": "do not retry until the cwd is corrected",
            },
        )


async def _run_with_timeout(
    process: asyncio.subprocess.Process, command: str, timeout_seconds: float
) -> ToolResult:
    """Drain the process under a timeout, returning success/error/timeout result."""
    # #25: consume stdout *concurrently* with execution via communicate().
    # Awaiting process.wait() before reading deadlocks any command that
    # writes more than the pipe buffer: the child blocks on write while we
    # block on exit. communicate() drains the pipe as the child runs.
    try:
        stdout_b, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        await _kill(process)
        partial = await _read_remaining(process)
        return ToolResult(
            content=_format_timeout(partial, command, timeout_seconds),
            is_error=True,
            metadata={
                "command": command,
                "returncode": process.returncode,
                "timed_out": True,
                "root_cause": f"command timed out after {timeout_seconds}s",
                "safe_retry": "rerun with a tighter scope or larger timeout",
                "stop_condition": "do not retry beyond the declared tool timeout",
            },
        )
    return _build_result(bytearray(stdout_b), command, process.returncode)


def _build_result(buffer: bytearray, command: str, returncode: int | None) -> ToolResult:
    """Assemble the success/error ``ToolResult`` from captured output."""
    text = _format(buffer)
    is_error = returncode != 0
    metadata: dict[str, Any] = {
        "command": command,
        "returncode": returncode,
        "timed_out": False,
    }
    if is_error:
        metadata.update(
            {
                "root_cause": f"exit code {returncode}",
                "safe_retry": "inspect output, adjust arguments, and rerun",
                "stop_condition": "do not retry on the same arguments after two failures",
            }
        )
    return ToolResult(content=text, is_error=is_error, metadata=metadata)


def _result_from_sandbox(
    outcome: SandboxResult, command: str, timeout_seconds: float
) -> ToolResult:
    """Map a :class:`SandboxResult` back into the bash ``ToolResult`` shape.

    The adapter path produces the same observable contract as the in-tool
    spawn path: combined stdout/stderr in ``content``, ``returncode`` /
    ``timed_out`` in ``metadata``, and the 3-part error contract on timeout
    or a non-zero exit.
    """
    if outcome.timed_out:
        partial = bytearray(_join_streams(outcome).encode("utf-8"))
        return ToolResult(
            content=_format_timeout(partial, command, timeout_seconds),
            is_error=True,
            metadata={
                "command": command,
                "returncode": outcome.returncode,
                "timed_out": True,
                "root_cause": f"command timed out after {timeout_seconds}s",
                "safe_retry": "rerun with a tighter scope or larger timeout",
                "stop_condition": "do not retry beyond the declared tool timeout",
            },
        )
    return _build_result(
        bytearray(_join_streams(outcome).encode("utf-8")), command, outcome.returncode
    )


def _join_streams(outcome: SandboxResult) -> str:
    """Combine stdout + stderr so stderr-only output never disappears.

    Both streams → join with a separator; otherwise whichever produced
    output (stderr wins when present), matching ``_compose_subprocess_result``.
    """
    if outcome.stderr and outcome.stdout:
        return f"{outcome.stdout}\n--- stderr ---\n{outcome.stderr}"
    return outcome.stderr or outcome.stdout


def _shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        return ["cmd.exe", "/c", command]
    return ["/bin/sh", "-c", command]


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    # The child may exit on its own between the timeout firing and kill(),
    # which raises ProcessLookupError; suppress that already-exited race.
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=2.0)


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
