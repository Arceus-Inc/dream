"""02 — skills: a workspace rule the agent discovers and applies.

A SKILL.md dropped in docs/skills/ becomes a one-line entry in the system
prompt catalogue; the agent loads the full playbook with the `skill` tool only
when the work makes it relevant, then follows it. Here the skill mandates a
header line for every new Python file — check the produced file to see it
applied.

Run:  uv run python consumer-facing-api/examples/02_skills.py
"""

from __future__ import annotations

import asyncio
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver

HEADER = "# COMPANY-HEADER-0001"

SKILL = f"""\
---
name: company-file-header
description: The mandatory header every new Python source file must carry.
when_to_use: Whenever you create or write any new Python source file.
---
# File-header convention

Every new Python file you create MUST begin with this EXACT line:

    {HEADER}

This is a hard, non-negotiable project rule.
"""


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()
    skill_dir = workspace / "docs" / "skills" / "company-file-header"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL, encoding="utf-8")
    print(f"workspace: {workspace}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        # skills=True is the default — shown explicitly for the example
        skills=True,
    )
    async with harness:
        await harness.run_task(
            intent="Create a Python module hello.py with a function that returns 'hello'.",
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )

    produced = workspace / "hello.py"
    text = produced.read_text(encoding="utf-8") if produced.exists() else ""
    applied = text.lstrip().startswith(HEADER)
    print(f"\nskill applied (header present): {applied}")
    print(f"first line: {text.splitlines()[0] if text else '<no file>'}")


if __name__ == "__main__":
    asyncio.run(main())
