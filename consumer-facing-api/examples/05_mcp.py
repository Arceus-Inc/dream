"""05 — MCP: wire a real server (Playwright) through the allowlist + trust ramp.

Requires Node (`npx`) on PATH — the official @playwright/mcp server is spawned
over stdio on first session. Two phases:

1. discovery: open a session, list the registered mcp__playwright__* tools;
2. promotion + live run: discovered MCP tools start read-only regardless of
   what they declare, so promote them in .harness/tool-tier-overrides.toml,
   then run a task where the generator drives the browser.

Run:  uv run python consumer-facing-api/examples/05_mcp.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys

from _common import fresh_workspace, load_creds

from dream import build_harness
from dream.runner import StdioObserver
from dream.tools.builtin import default_registry

ALLOWLIST = """\
[[mcp]]
name      = "playwright"
endpoint  = "stdio://npx -y @playwright/mcp@latest --headless --isolated"
transport = "stdio"
"""

PAGE = "<!doctype html><title>MCP-DEMO-TITLE</title><h1>MCP-DEMO-TITLE</h1>"


def promote(workspace, names) -> None:
    body = "".join(
        f'["{name}"]\ntier_required = "repo-write"\n'
        'promoted_by = "example"\nreason = "browser demo"\n\n'
        for name in names
    )
    (workspace / ".harness" / "tool-tier-overrides.toml").write_text(body, encoding="utf-8")


async def main() -> None:
    if shutil.which("npx") is None:
        raise SystemExit("npx (Node) not on PATH — required for @playwright/mcp")
    creds = load_creds()
    workspace = fresh_workspace()
    (workspace / ".harness" / "mcp-allowlist.toml").write_text(ALLOWLIST, encoding="utf-8")
    page = workspace / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    print(f"workspace: {workspace}\n")

    # Phase 1 — connect + discover tool names (first npx run may take a while).
    registry = default_registry()
    probe = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=workspace, registry=registry, plugins=False,
    )
    await probe.start_session()
    mcp_tools = sorted(t.name for t in registry.list_tools() if t.name.startswith("mcp__"))
    await probe.aclose()
    print(f"discovered {len(mcp_tools)} MCP tools, e.g. {mcp_tools[:5]}\n")

    # Phase 2 — promote them past the trust ramp, then drive the browser live.
    promote(workspace, mcp_tools)
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=workspace, plugins=False,
    )
    async with harness:
        await harness.run_task(
            intent=(
                "Use the mcp__playwright__ browser tools to open "
                f"{page.as_uri()} , take a page snapshot, and report the exact "
                "<title> text. Do not read the HTML file directly."
            ),
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )


if __name__ == "__main__":
    asyncio.run(main())
