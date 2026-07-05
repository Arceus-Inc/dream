"""Default ``grep`` tool — regex content search, ripgrep with a Python fallback.

Read-only (tier 0, safe): searching never mutates the tree. Shape borrowed from
OpenHarness ``grep_tool.py`` and adapted to dream's contract — the model-supplied
root is confined to the working directory via :func:`resolve_within` before any
read, ripgrep is preferred for speed (respecting ``.gitignore``), and a pure
Python walk is the portable fallback when ``rg`` is absent or errors.

Invalid regex and a missing root surface the Spec 05 three-part error contract
rather than raising.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within
from dream.tools.builtin._errors import tool_error

# Bytes sampled from a file prefix to mark it binary and skip it (Python path).
_BINARY_SNIFF = 8192


class GrepInput(BaseModel):
    """Arguments for the ``grep`` tool."""

    pattern: str = Field(description="Regular expression to search for.")
    path: str | None = Field(
        default=None,
        description="Search root (dir or file), within the working directory. "
        "Defaults to the working directory.",
    )
    glob: str = Field(default="**/*", description="Only search files matching this glob.")
    case_sensitive: bool = Field(default=True)
    limit: int = Field(default=200, ge=1, le=2000, description="Max matching lines to return.")


class GrepTool(BaseTool):
    """Search text files for a regex pattern, returning ``path:line:text``."""

    name = "grep"
    description = "Search file contents with a regular expression."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=20.0)
    input_model = GrepInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        # Report the search root so the credential guard can evaluate it (the
        # guard is effect-agnostic; a read still declares the path it touches).
        args = GrepInput.model_validate(input)
        return ToolEffects(target_paths=(Path(args.path or "."),))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = GrepInput.model_validate(input)
        try:
            root = resolve_within(ctx.working_dir, args.path or ".")
        except PathEscapesRoot as exc:
            return tool_error(
                f"Path outside the working directory: {args.path}",
                root_cause=str(exc),
                safe_retry="pass a path within the working directory, or omit it",
                stop_condition="do not retry with the same out-of-tree path",
            )
        if not root.exists():
            return tool_error(
                f"Search root does not exist: {root}",
                root_cause=f"path does not exist: {root}",
                safe_retry="verify the path, or omit it to search the working directory",
                stop_condition="do not retry with the same missing path",
            )

        matches = await _ripgrep(root, args)
        if matches is None:
            result = _python_grep(root, args)
            if isinstance(result, ToolResult):
                return result
            matches = result

        if not matches:
            return ToolResult(
                content="(no matches)",
                metadata={"match_count": 0, "summary": f"no matches for {args.pattern!r}"},
            )
        return ToolResult(
            content="\n".join(matches),
            metadata={
                "match_count": len(matches),
                "truncated": len(matches) >= args.limit,
                "summary": f"{len(matches)} match(es) for {args.pattern!r}",
            },
        )


async def _ripgrep(root: Path, args: GrepInput) -> list[str] | None:
    """Search with ripgrep; return matches, or ``None`` if rg is unavailable/errors."""
    rg = shutil.which("rg")
    if rg is None:
        return None

    is_file = root.is_file()
    base = root.parent if is_file else root
    cmd = [rg, "--no-heading", "--line-number", "--color", "never"]
    if not is_file and ((root / ".git").exists() or (root / ".gitignore").exists()):
        cmd.append("--hidden")
    if not args.case_sensitive:
        cmd.append("-i")
    if not is_file and args.glob:
        cmd.extend(["--glob", args.glob])
    cmd.extend(["--", args.pattern, root.name if is_file else "."])

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(base),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,  # 8 MB/line ceiling — long minified lines
        )
    except OSError:
        return None

    matches: list[str] = []
    prefix = f"{root.name}:" if is_file else ""
    try:
        await asyncio.wait_for(
            _collect(process, matches, limit=args.limit, prefix=prefix),
            timeout=float(GrepTool.declaration.timeout_seconds),
        )
    except TimeoutError:
        await _terminate(process)
    finally:
        if process.returncode is None:
            await _terminate(process)

    # rg: 0 = matches, 1 = none, negative = signalled by us. Anything else is a
    # real error (e.g. bad regex) → fall back to Python for a clean message.
    if process.returncode not in {0, 1, -15, -9}:
        return None
    return matches


async def _collect(
    process: asyncio.subprocess.Process, matches: list[str], *, limit: int, prefix: str
) -> None:
    assert process.stdout is not None
    while len(matches) < limit:
        try:
            raw = await process.stdout.readline()
        except ValueError:
            continue  # line exceeded buffer; skip it
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n").removeprefix("./")
        if line:
            matches.append(f"{prefix}{line}")


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


def _python_grep(root: Path, args: GrepInput) -> list[str] | ToolResult:
    """Portable fallback: compile the pattern and walk files under ``root``."""
    flags = 0 if args.case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(args.pattern, flags)
    except re.error as exc:
        return tool_error(
            f"Invalid regex pattern: {args.pattern!r}",
            root_cause=f"regex compile error: {exc}",
            safe_retry="fix the pattern syntax and retry",
            stop_condition="do not retry with the same invalid pattern",
        )

    paths = [root] if root.is_file() else sorted(root.glob(args.glob))
    matches: list[str] = []
    for path in paths:
        if len(matches) >= args.limit:
            break
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:_BINARY_SNIFF]:
            continue
        rel = _display(path, root)
        for line_no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if compiled.search(line):
                matches.append(f"{rel}:{line_no}:{line}")
                if len(matches) >= args.limit:
                    break
    return matches


def _display(path: Path, root: Path) -> str:
    base = root.parent if root.is_file() else root
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


__all__ = ["GrepInput", "GrepTool"]
