"""04 — plugins: a repo-local tool the agent discovers and calls.

A plugin is a directory under plugins/ with a manifest and an entry module
exposing get_plugin(manifest) -> Plugin. It must be opted in via
.harness/plugins-enabled.toml and its declared capabilities must fit the
sandbox tier. Its tools enter the registry as *discovered* (trust ramp) and
become callable by the generator.

Run:  uv run python consumer-facing-api/examples/04_plugins.py
"""

from __future__ import annotations

import asyncio
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver

MANIFEST = """\
name        = "ticket-stamper"
version     = "0.1.0"
entry       = "main.py"
description = "Stamp the current ticket id into the workspace."

[capabilities]
required = ["repo-write"]
"""

# The plugin tool writes a file the model could not produce by itself — proof
# in the workspace that the *tool* ran, not a lookalike.
ENTRY = '''\
from typing import Any

from pydantic import BaseModel

from dream.contracts.plugin import Plugin
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class _In(BaseModel):
    pass


class StampTicketTool(BaseTool):
    name = "stamp_ticket"
    description = (
        "Stamp the current ticket id into TICKET.txt. Call this whenever a "
        "request asks you to stamp or record the ticket."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        (ctx.working_dir / "TICKET.txt").write_text("TICKET-4242\\n", encoding="utf-8")
        return ToolResult(content="stamped TICKET-4242")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(StampTicketTool(),))
'''


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()

    plugin_dir = workspace / "plugins" / "ticket-stamper"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (plugin_dir / "main.py").write_text(ENTRY, encoding="utf-8")
    enabled = workspace / ".harness" / "plugins-enabled.toml"
    enabled.write_text('[[plugin]]\nname = "ticket-stamper"\n', encoding="utf-8")
    print(f"workspace: {workspace}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        plugins=True,  # the default — shown explicitly
    )
    async with harness:
        await harness.run_task(
            intent=(
                "Stamp the current ticket by calling the stamp_ticket tool, "
                "then confirm it ran. Use the tool itself — do not write the "
                "file by hand."
            ),
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )

    ticket = workspace / "TICKET.txt"
    print(f"\nplugin tool ran: {ticket.exists() and 'TICKET-4242' in ticket.read_text()}")


if __name__ == "__main__":
    asyncio.run(main())
