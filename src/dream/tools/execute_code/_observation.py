"""Harness observation helpers for execute_code outcomes."""

from __future__ import annotations

from dream.tools.execute_code._types import ExecuteCodeStatus

__all__ = ["next_actions_for", "summary_for"]


def summary_for(
    status: ExecuteCodeStatus,
    *,
    exit_code: int,
    tool_calls_made: int,
    detail: str = "",
) -> str:
    """One-line parent-facing summary."""
    base = f"execute_code {status.value} · exit={exit_code} · calls={tool_calls_made}"
    detail = detail.strip()
    return f"{base} — {detail}" if detail else base


def next_actions_for(status: ExecuteCodeStatus) -> list[str]:
    """Actionable follow-ups for non-success (and empty for success)."""
    if status is ExecuteCodeStatus.SUCCESS:
        return []
    if status is ExecuteCodeStatus.TIMEOUT:
        return [
            "Retry with a smaller script or fewer nested tool calls",
            "Raise timeout only if the work genuinely needs more wall time",
        ]
    if status is ExecuteCodeStatus.CAP_EXCEEDED:
        return [
            "Reduce nested tool calls or split work across multiple execute_code turns",
        ]
    if status is ExecuteCodeStatus.REFUSED:
        return [
            "Use dream_tools.bash / web_* for shell and network instead of raw APIs",
            "Ensure the session role allowlist intersects the execute_code sandbox allowlist",
        ]
    if status is ExecuteCodeStatus.CANCELLED:
        return ["Re-run the script if the cancel was unintentional"]
    return [
        "Inspect stderr in the structured outcome and fix the script error",
        "Prefer dream_tools stubs over reinventing file/shell I/O",
    ]
