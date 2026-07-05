"""Default ``todo_write`` tool — maintain a markdown checklist in the workspace.

Mutating (tier 1): appends a new item or flips an existing one to done in a
markdown checklist file (``TODO.md`` by default). Shape borrowed from OpenHarness
``todo_write_tool.py`` and adapted to dream's contract — the target path is
confined to the working directory via :func:`confine_path`, and the write is
reported through :meth:`effects_for` so the repo-write boundary gate applies.

For durable, cross-session facts use ``propose_memory``; this is the lightweight,
in-repo task list the agent ticks off as it works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import confine_path
from dream.utils.fs import atomic_write_text

_SEED = "# TODO\n"


class TodoWriteInput(BaseModel):
    """Arguments for the ``todo_write`` tool."""

    item: str = Field(description="The TODO item text (without the checkbox prefix).")
    checked: bool = Field(default=False, description="Whether the item is done.")
    path: str = Field(default="TODO.md", description="Checklist file, within the workspace.")


class TodoWriteTool(BaseTool):
    """Add a TODO item, or mark an existing one done, in a markdown checklist."""

    name = "todo_write"
    description = (
        "Add a new TODO item or mark an existing one as done in a markdown "
        "checklist file (default TODO.md)."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = TodoWriteInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        args = TodoWriteInput.model_validate(input)
        return ToolEffects(target_paths=(Path(args.path),))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = TodoWriteInput.model_validate(input)
        path = confine_path(ctx.working_dir, args.path)
        if isinstance(path, ToolResult):
            return path

        existing = path.read_text(encoding="utf-8") if path.exists() else _SEED
        unchecked = f"- [ ] {args.item}"
        checked = f"- [x] {args.item}"
        target = checked if args.checked else unchecked

        if args.checked and unchecked in existing:
            updated = existing.replace(unchecked, checked, 1)
            summary = "checked off"
        elif target in existing:
            return ToolResult(
                content=f"No change needed in {_rel(path, ctx.working_dir)}",
                metadata={"changed": False, "summary": "already in desired state"},
            )
        else:
            updated = existing.rstrip("\n") + f"\n{target}\n"
            summary = "added"

        atomic_write_text(path, updated)
        return ToolResult(
            content=f"Updated {_rel(path, ctx.working_dir)} ({summary}: {args.item})",
            metadata={"changed": True, "summary": f"{summary} TODO item"},
        )


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


__all__ = ["TodoWriteInput", "TodoWriteTool"]
