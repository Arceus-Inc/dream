"""09 — working memory: the task scratchpad + the memory_propose seam (opt-in).

`build_harness(working_memory=True)` gives the agent its own task-memory tier
(spec 11a) — dream's one and only memory clock:

  - working_memory_read / _write / _append  → a `working-memory.md` scratchpad
    that lives and dies with the worktree (the agent's mid-task cognition).
  - memory_propose  → an OUTBOUND seam: the agent nominates a durable fact for
    promotion. It lands in a `_proposals/` queue under the dream home (which
    survives worktree teardown) — it is NOT applied now.

The boundary is deliberate: **dream proposes, never promotes.** Your repo
(`lattice`/`chorus`/`horizon`, Model A) drains the queue and decides what to
promote into the durable store. This example plays that consumer role: it runs
a task that proposes a convention, then reads the queue the way your curation
loop would.

Oracle (unforgeable): a proposal file with the requested slug appears in the
`_proposals/` queue — the agent could only produce that by calling memory_propose.

Run:  uv run python consumer-facing-api/examples/09_working_memory.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.config.paths import DreamPaths
from dream.memory import proposals_dir
from dream.runner import StdioObserver

SLUG = "api-routes-location"

_INTENT = (
    "This project follows a convention worth remembering for future tasks: all "
    "HTTP API routes live under `src/api/`. Steps: (1) record what you learned "
    "in your working memory with the working_memory_append tool; (2) nominate "
    "this convention as a durable memory entry by calling the memory_propose "
    f"tool with slug='{SLUG}', a one-sentence content describing the rule, and "
    "a rationale for why future tasks should remember it. You do not need to "
    "create any files."
)


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()
    # The proposals queue lives under the dream home, keyed by this project —
    # the exact directory your curation loop would drain.
    paths = DreamPaths.resolve(workspace, env=os.environ).ensure()
    queue = proposals_dir(paths.home, workspace)
    print(f"workspace: {workspace}\nproposals: {queue}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        working_memory=True,  # opt in — off by default
    )
    async with harness:
        await harness.run_task(
            intent=_INTENT,
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )

    # Play the consumer: drain the proposals queue the agent wrote into.
    proposals = sorted(queue.glob("*.md")) if queue.exists() else []
    matched = [p for p in proposals if SLUG in p.name]
    print("\n--- task-memory check ---")
    print(f"proposals in queue: {[p.name for p in proposals]}")
    print(f"memory_propose fired (slug '{SLUG}'): {bool(matched)}")
    if matched:
        print("\n--- proposal your repo would now review & promote ---")
        print(matched[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
