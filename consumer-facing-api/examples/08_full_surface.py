"""08 — everything at once: skills + memory + plugin + hooks + sandboxed bash.

One workspace wired with every component, one run_task that needs all of them:
the skill names the report file, project memory holds the audit token, a
plugin tool emits the proof marker, the bash tool (sandbox-routed) verifies,
and a hook meters every dispatch from inside the engine. (MCP is the one
surface left out here — it needs Node; see 05_mcp.py.)

Run:  uv run python consumer-facing-api/examples/08_full_surface.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from _common import fresh_workspace, load_creds

from dream import HookEvent, HookResult, HookSpec, build_harness
from dream.config.paths import DreamPaths
from dream.memory import project_memory_dir
from dream.runner import StdioObserver

TOKEN = "AUDIT-TOKEN-77"

SKILL = """\
---
name: report-naming
description: The required filename for any report file in this project.
when_to_use: Whenever you create a report or summary file.
---
# Report naming rule
Any report file MUST be named exactly `audit_report.txt`.
"""

MEMORY = f"""\
---
name: audit-token
description: the audit token that must appear in every report
metadata:
  type: project
  scope: project
---
The project's audit token is `{TOKEN}`. Every report must contain it.
"""

PLUGIN_MANIFEST = """\
name        = "proof-emitter"
version     = "0.1.0"
entry       = "main.py"
description = "Emit the proof marker."

[capabilities]
required = ["repo-write"]
"""

PLUGIN_ENTRY = '''\
from typing import Any

from pydantic import BaseModel

from dream.contracts.plugin import Plugin
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class _In(BaseModel):
    pass


class EmitProofTool(BaseTool):
    name = "emit_proof"
    description = "Emit the project proof marker. Call this when asked to emit the proof marker."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="proof marker emitted")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(EmitProofTool(),))
'''

INTENT = (
    "Produce this project's audit report: (1) the audit token is stored only "
    "in project memory — retrieve it with your memory tools; (2) emit the "
    "proof marker by calling the emit_proof tool; (3) run `echo audit-complete` "
    "with the bash tool; (4) write a report file containing the audit token, "
    "following the project's report-naming convention (a skill documents it)."
)


class MeterHook:
    spec = HookSpec(events=(HookEvent.PRE_TOOL_USE,))

    def __init__(self) -> None:
        self.dispatches: list[str] = []

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        self.dispatches.append(str(payload.get("tool_name", "?")))
        return HookResult()


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()

    # skill
    skill_dir = workspace / "docs" / "skills" / "report-naming"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL, encoding="utf-8")
    # memory
    paths = DreamPaths.resolve(workspace, env=os.environ).ensure()
    memory_dir = project_memory_dir(paths.home, workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "audit-token.md").write_text(MEMORY, encoding="utf-8")
    # plugin
    plugin_dir = workspace / "plugins" / "proof-emitter"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.toml").write_text(PLUGIN_MANIFEST, encoding="utf-8")
    (plugin_dir / "main.py").write_text(PLUGIN_ENTRY, encoding="utf-8")
    (workspace / ".harness" / "plugins-enabled.toml").write_text(
        '[[plugin]]\nname = "proof-emitter"\n', encoding="utf-8"
    )
    print(f"workspace: {workspace}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
    )
    meter = MeterHook()
    harness.register_hook(meter)

    async with harness:
        await harness.run_task(
            intent=INTENT, observer=StdioObserver(sys.stdout), max_sprints=5
        )

    report = workspace / "audit_report.txt"
    text = report.read_text(encoding="utf-8") if report.exists() else ""
    print("\n--- surface check ---")
    print(f"skills:  report named audit_report.txt: {report.exists()}")
    print(f"memory:  token in report: {TOKEN in text}")
    print(f"plugins: emit_proof dispatched: {'emit_proof' in meter.dispatches}")
    print(f"sandbox: bash dispatched: {'bash' in meter.dispatches}")
    print(f"hooks:   {len(meter.dispatches)} dispatches metered from inside the engine")


if __name__ == "__main__":
    asyncio.run(main())
