"""Default ``glob`` tool — list files matching a glob, ripgrep-walked when able.

Read-only (tier 0, safe). Shape borrowed from OpenHarness ``glob_tool.py`` and
adapted to dream's contract: the search root is confined to the working directory
via :func:`resolve_within`, ``rg --files`` walks the tree for recursive patterns
(respecting ``.gitignore`` so ``.venv`` / ``node_modules`` don't explode the
result set), and ``Path.glob`` is the portable fallback. Results are sorted so
output is deterministic.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within
from dream.tools.builtin._errors import tool_error

_RG_TIMEOUT = 20.0


class GlobInput(BaseModel):
    """Arguments for the ``glob`` tool."""

    pattern: str = Field(description="Glob pattern, e.g. '**/*.py' or 'src/*.ts'.")
    path: str | None = Field(
        default=None,
        description="Search root within the working directory. Defaults to it.",
    )
    limit: int = Field(default=200, ge=1, le=5000, description="Max paths to return.")


class GlobTool(BaseTool):
    """List files matching a glob pattern, relative to the search root."""

    name = "glob"
    description = "List files matching a glob pattern."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=20.0)
    input_model = GlobInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = GlobInput.model_validate(input)
        return ToolEffects(target_paths=(Path(args.path or "."),))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = GlobInput.model_validate(input)
        try:
            root = resolve_within(ctx.working_dir, args.path or ".")
        except PathEscapesRoot as exc:
            return tool_error(
                f"Path outside the working directory: {args.path}",
                root_cause=str(exc),
                safe_retry="pass a path within the working directory, or omit it",
                stop_condition="do not retry with the same out-of-tree path",
            )
        if not root.is_dir():
            return tool_error(
                f"Search root is not a directory: {root}",
                root_cause=f"path is missing or not a directory: {root}",
                safe_retry="pass a directory path, or omit it to use the working directory",
                stop_condition="do not retry with the same non-directory path",
            )

        matches = await _ripgrep_files(root, args.pattern, args.limit)
        if matches is None:
            matches = _python_glob(root, args.pattern, args.limit)

        if not matches:
            return ToolResult(
                content="(no matches)",
                metadata={"match_count": 0, "summary": f"no files match {args.pattern!r}"},
            )
        return ToolResult(
            content="\n".join(matches),
            metadata={
                "match_count": len(matches),
                "truncated": len(matches) >= args.limit,
                "summary": f"{len(matches)} file(s) match {args.pattern!r}",
            },
        )


async def _ripgrep_files(root: Path, pattern: str, limit: int) -> list[str] | None:
    """Walk with ``rg --files --glob``; return matches or ``None`` if unavailable."""
    rg = shutil.which("rg")
    # rg's walker only earns its keep for recursive patterns; the plain
    # ``Path.glob`` fallback is cheap for shallow, single-segment patterns.
    if rg is None or not ("**" in pattern or "/" in pattern):
        return None

    cmd = [rg, "--files"]
    if (root / ".git").exists():
        cmd.append("--hidden")
    cmd.extend(["--glob", pattern, "."])
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None

    lines: list[str] = []
    try:
        await asyncio.wait_for(_collect(process, lines, limit=limit), timeout=_RG_TIMEOUT)
    except TimeoutError:
        await _terminate(process)
    finally:
        if process.returncode is None:
            await _terminate(process)

    if process.returncode not in {0, 1, -15, -9}:
        return None
    return sorted(lines)


async def _collect(
    process: asyncio.subprocess.Process, lines: list[str], *, limit: int
) -> None:
    assert process.stdout is not None
    while len(lines) < limit:
        raw = await process.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip().removeprefix("./")
        if line:
            lines.append(line)


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


def _python_glob(root: Path, pattern: str, limit: int) -> list[str]:
    """Portable fallback using ``Path.glob``; files only, sorted, capped."""
    out: list[str] = []
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            out.append(str(path.relative_to(root)))
            if len(out) >= limit:
                break
    return out


__all__ = ["GlobInput", "GlobTool"]
