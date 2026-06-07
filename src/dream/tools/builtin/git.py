"""Default ``git`` tool — read-only subcommand allowlist via ``utils.git``.

Spec 05 slice B. Routes through ``dream.utils.git.run_git`` so all
subprocess invocations stay in the single auditable wrapper (spec 01
invariant). The subcommand allowlist is intentionally read-only: mutating
git operations (push, commit, reset, checkout) are out of scope for the
default tool and would need a separate, higher-tier tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.utils.git import run_git

# A validator inspects the args *after* the subcommand and returns an error
# reason for a mutating/disallowed form, or ``None`` when read-only.
_Validator = Callable[[list[str]], "str | None"]

# Subcommands whose every invocation form is read-only. They take only
# pathspecs / read flags, so the bare presence on the allowlist is sufficient
# and no per-argument gate is required.
_ALWAYS_READ_ONLY: frozenset[str] = frozenset(
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

# Subcommands that have BOTH read-only and mutating forms. Each maps to a
# validator that returns an error reason (str) for a mutating/disallowed
# invocation, or ``None`` when the specific args are read-only. This keeps the
# read-only contract honest: a tool declared ``risk="safe"`` must never mutate.
_REJECT_BRANCH_TAG = (
    "this form mutates refs; only the listing form (no name, or "
    "--list / -l / -a / -r / -v / --contains ...) is allowed"
)


def _validate_branch(rest: list[str]) -> str | None:
    """``git branch`` is read-only only as a listing; naming a ref creates it."""
    listing_flags = {"--list", "-l", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose"}
    # Flags that take a value but stay read-only (e.g. --contains <commit>).
    read_value_flags = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
    positionals: list[str] = []
    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok in read_value_flags:
            skip_next = True
            continue
        if tok in listing_flags or tok == "--":
            continue
        if tok.startswith("-"):
            # Unknown flag (e.g. -d/-D/-m/--move/--copy/--edit-description): reject.
            return _REJECT_BRANCH_TAG
        positionals.append(tok)
    # Any positional names a branch to create/rename/delete → mutating.
    if positionals:
        return _REJECT_BRANCH_TAG
    return None


def _validate_tag(rest: list[str]) -> str | None:
    """``git tag`` is read-only only as a listing; naming a tag creates it.

    Bare ``git tag`` lists. With an explicit listing flag (``-l`` / ``--list``
    / ``-n`` / a filter flag), any positionals are *patterns*, so the form
    stays read-only. Without a listing flag, a positional names a tag to
    create, and ``-d`` / ``-a`` / ``-s`` / ``-m`` / ``-f`` mutate.
    """
    listing_flags = {"--list"}
    value_flags = {"--contains", "--no-contains", "--points-at", "--merged", "--no-merged"}
    has_listing = False
    positionals: list[str] = []
    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok in value_flags:
            has_listing = True
            skip_next = True
            continue
        if tok == "-l" or tok in listing_flags or tok.startswith("-n"):
            # -l / --list / -n / -n<num>: listing modifiers.
            has_listing = True
            continue
        if tok == "--":
            continue
        if tok.startswith("-"):
            # -d/-a/-s/-m/-f etc. all create or delete tags → reject.
            return _REJECT_BRANCH_TAG
        positionals.append(tok)
    # Positionals are read-only patterns only when a listing flag is present;
    # otherwise the first positional names a tag to create.
    if positionals and not has_listing:
        return _REJECT_BRANCH_TAG
    return None


def _validate_config(rest: list[str]) -> str | None:
    """``git config`` mutates unless it is purely a read (--get/--list)."""
    read_flags = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch"}
    if not rest:
        return None
    if any(tok in read_flags for tok in rest):
        # Even a read must not target an alternate scope for writing; --get is
        # inherently read-only regardless of scope, so allow it.
        return None
    return (
        "git config without an explicit read flag writes configuration; "
        "use --get / --get-all / --get-regexp / --list"
    )


def _validate_remote(rest: list[str]) -> str | None:
    """``git remote`` is read-only as bare listing or ``-v`` / ``show`` / ``get-url``."""
    if not rest:
        return None
    read_actions = {"show", "get-url"}
    first = rest[0]
    if first in {"-v", "--verbose"}:
        return None
    if first in read_actions:
        return None
    return (
        "this git remote form mutates remotes (add/remove/rename/set-url/prune); "
        "only bare 'remote', 'remote -v', 'remote show', 'remote get-url' are allowed"
    )


def _validate_stash(rest: list[str]) -> str | None:
    """``git stash`` is read-only only as ``list`` or ``show``."""
    read_actions = {"list", "show"}
    if not rest:
        # Bare ``git stash`` is shorthand for ``stash push`` → mutating.
        return "bare 'git stash' pushes a stash; only 'stash list' / 'stash show' are allowed"
    if rest[0] in read_actions:
        return None
    return "this git stash form mutates the stash; only 'stash list' / 'stash show' are allowed"


# Subcommands with mixed read/write forms, each guarded by a validator.
_GATED_SUBCOMMANDS: dict[str, _Validator] = {
    "branch": _validate_branch,
    "tag": _validate_tag,
    "config": _validate_config,
    "remote": _validate_remote,
    "stash": _validate_stash,
}


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

    ALLOWED_SUBCOMMANDS: ClassVar[frozenset[str]] = _ALWAYS_READ_ONLY | frozenset(
        _GATED_SUBCOMMANDS
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

        validator = _GATED_SUBCOMMANDS.get(subcommand)
        if validator is not None and (reason := validator(args.args[1:])) is not None:
            return _err(
                f"git {subcommand} invocation not allowed: {reason}",
                root_cause=f"{subcommand!r} called in a mutating form",
                safe_retry=reason,
                stop_condition="do not retry with a mutating git invocation",
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
