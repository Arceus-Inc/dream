"""03 — workspace memory: a fact the agent can only learn from memory.

A markdown record is seeded into the project memory dir (outside the repo,
under DREAM_HOME). Its description appears in the system-prompt catalogue; the
agent pulls the full record with memory_search / memory_get and applies it.
Here the record holds the project's required service-naming convention.

Run:  uv run python consumer-facing-api/examples/03_memory.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.config.paths import DreamPaths
from dream.memory import project_memory_dir
from dream.runner import StdioObserver

RECORD = """\
---
name: naming-convention
description: microservices in this project use a service- prefix
metadata:
  type: project
  scope: project
---

All microservices in this repo MUST be named `service-<domain>`,
e.g. service-billing, service-auth. Never `<domain>-service`.
"""


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()

    # Seed one memory record into the per-project memory dir.
    paths = DreamPaths.resolve(workspace, env=os.environ).ensure()
    memory_dir = project_memory_dir(paths.home, workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "naming-convention.md").write_text(RECORD, encoding="utf-8")
    print(f"workspace: {workspace}\nmemory:    {memory_dir}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        memory=True,  # the default — shown explicitly
    )
    async with harness:
        await harness.run_task(
            intent=(
                "Scaffold a new microservice that handles invoices: create a "
                "directory named after the service (follow this project's "
                "naming conventions) containing an empty __init__.py."
            ),
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )

    dirs = [p.name for p in workspace.iterdir() if p.is_dir() and not p.name.startswith(".")]
    print(f"\nproduced dirs: {dirs}")
    print(f"memory applied (service- prefix): {any(d.startswith('service-') for d in dirs)}")


if __name__ == "__main__":
    asyncio.run(main())
