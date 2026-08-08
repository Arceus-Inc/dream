"""Human-readable summaries of applied commits."""

from __future__ import annotations

from dream.tools.apply_patch._types import ActionType, Commit


def summarize_commit(commit: Commit) -> str:
    parts: list[str] = []
    for path, change in commit.changes.items():
        if change.type == ActionType.ADD:
            parts.append(f"added {path}")
        elif change.type == ActionType.DELETE:
            parts.append(f"deleted {path}")
        elif change.move_path:
            parts.append(f"moved {path} -> {change.move_path}")
        else:
            parts.append(f"updated {path}")
    return "; ".join(parts) if parts else "no changes"


__all__ = ["summarize_commit"]
