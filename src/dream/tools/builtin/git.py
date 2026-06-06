"""Default ``git`` tool — read-only subcommand allowlist via ``utils.git``.

Spec 05 slice B. Routes through ``dream.utils.git.run_git`` so all
subprocess invocations stay in the single auditable wrapper (spec 01
invariant). The subcommand allowlist is intentionally read-only: mutating
git operations (push, commit, reset, checkout) are out of scope for the
default tool and would need a separate, higher-tier tool.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.utils.git import run_git


class GitInput(BaseModel):
    """Arguments for the ``git`` tool."""

    args: list[str] = Field(
        description="git arguments. First element must be an allowed subcommand.",
    )


class GitTool(BaseTool):
    """Run a read-only git subcommand from a fixed allowlist."""

    name = "git"
    description = "Run a read-only git subcommand (status, diff, log, ...)."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=30.0)
    input_model = GitInput

    ALLOWED_SUBCOMMANDS: ClassVar[frozenset[str]] = frozenset(
        {
            "status",
            "diff",
            "log",
            "show",
            "branch",
            "tag",
            "rev-parse",
            "ls-files",
            "ls-tree",
            "config",
            "describe",
            "blame",
            "remote",
            "stash",
        }
    )

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = GitInput.model_validate(input)
        if not args.args:
            return _err(
                "git tool requires at least one argument (subcommand)",
                root_cause="empty args list",
                safe_retry="pass at least the subcommand, e.g. {'args': ['status']}",
                stop_condition="do not retry with empty args",
            )
        subcommand = args.args[0]
        if subcommand not in self.ALLOWED_SUBCOMMANDS:
            allowed = ", ".join(sorted(self.ALLOWED_SUBCOMMANDS))
            return _err(
                f"git subcommand not allowed: {subcommand!r}",
                root_cause=f"{subcommand!r} is not in the read-only allowlist",
                safe_retry=f"use one of: {allowed}",
                stop_condition="do not retry with a mutating git subcommand",
            )

        returncode, stdout, stderr = run_git(args.args, cwd=ctx.working_dir)
        is_error = returncode != 0
        if stdout and stderr:
            content = f"{stdout}\n--- stderr ---\n{stderr}"
        else:
            content = stdout or stderr or "(no output)"
        metadata: dict[str, Any] = {
            "returncode": returncode,
            "subcommand": subcommand,
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
        }
        if is_error:
            metadata.update(
                {
                    "root_cause": f"git {subcommand} exit {returncode}",
                    "safe_retry": "inspect stderr and adjust arguments",
                    "stop_condition": "do not retry with the same arguments",
                }
            )
        return ToolResult(content=content, is_error=is_error, metadata=metadata)


def _err(content: str, *, root_cause: str, safe_retry: str, stop_condition: str) -> ToolResult:
    return ToolResult(
        content=content,
        is_error=True,
        metadata={
            "root_cause": root_cause,
            "safe_retry": safe_retry,
            "stop_condition": stop_condition,
        },
    )


__all__ = ["GitInput", "GitTool"]
