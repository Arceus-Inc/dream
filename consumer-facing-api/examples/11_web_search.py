"""11 — web_search: the agent pulls a live fact off the web, then writes it down.

The ``web_search`` built-in is Tavily-backed and declared ``external`` (it
leaves the machine), so it needs the network sandbox tier. This example wires a
workspace at ``repo-write+net-allowlist`` — that tier both permits the network
effect *and* auto-trusts the built-in for it, so the search runs with no
interactive approval. The agent searches, then writes what it found to a file
we verify afterwards.

Extra credential (beyond the model creds every example needs):

    export DREAM_TAVILY_API_KEY=tvly-...   # or TAVILY_API_KEY

Run:  uv run python consumer-facing-api/examples/11_web_search.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver

INTENT = (
    "Find the latest stable Python version. Use the web_search tool to look it "
    "up, then write a file named `python_version.txt` containing exactly two "
    "lines: the version number on the first line, and the source URL you found "
    "it at on the second line."
)


async def main() -> None:
    creds = load_creds()
    if not (os.environ.get("DREAM_TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY")):
        raise SystemExit(
            "web_search needs a Tavily key: export DREAM_TAVILY_API_KEY=tvly-..."
        )

    # tier 2 (repo-write+net-allowlist): permits the network effect and
    # auto-trusts the web_search built-in for it — no approval prompt.
    workspace = fresh_workspace(tier="repo-write+net-allowlist")
    print(f"workspace: {workspace}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        web=True,
    )
    async with harness:
        result = await harness.run_task(
            intent=INTENT,
            observer=StdioObserver(sys.stdout),
            max_sprints=4,
        )

    findings = workspace / "python_version.txt"
    text = findings.read_text(encoding="utf-8").strip() if findings.exists() else ""
    print(f"\ntask {result.task_id} finished in {len(result.sprints)} sprint(s)")
    print("\n--- web_search check ---")
    print(f"file written: {findings.exists()}")
    if text:
        print("--- python_version.txt ---")
        print(text)


if __name__ == "__main__":
    asyncio.run(main())
