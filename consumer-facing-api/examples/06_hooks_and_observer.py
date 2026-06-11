"""06 — hooks + observer: watch the loop from inside and outside.

Two complementary surfaces:
- a HOOK fires *inside* the engine loop (session start/stop, strictly around
  every tool dispatch) — observer-only, crash-isolated, never vetoes;
- an OBSERVER receives the runner's macro events (planner/sprint boundaries,
  streamed text, head retries, escalations) *outside* the engine.

Run:  uv run python consumer-facing-api/examples/06_hooks_and_observer.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from _common import fresh_workspace, load_creds

from dream import HookEvent, HookResult, HookSpec, build_harness
from dream.runner import StdioObserver


class ToolMeterHook:
    """Count tool dispatches per tool name, from inside the engine."""

    spec = HookSpec(
        events=(
            HookEvent.SESSION_START,
            HookEvent.PRE_TOOL_USE,
            HookEvent.POST_TOOL_USE,
            HookEvent.STOP,
        )
    )

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.sessions = 0

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        if event is HookEvent.SESSION_START:
            self.sessions += 1
        elif event is HookEvent.PRE_TOOL_USE:
            tool = str(payload.get("tool_name", "?"))
            self.counts[tool] = self.counts.get(tool, 0) + 1
        return HookResult()


class EventRecorder:
    """Keep every macro event while still streaming to stdout."""

    def __init__(self) -> None:
        self._stdio = StdioObserver(sys.stdout)
        self.kinds: list[str] = []

    def on_event(self, event: dict[str, Any]) -> None:
        self.kinds.append(event.get("kind", ""))
        self._stdio.on_event(event)


async def main() -> None:
    creds = load_creds()
    workspace = fresh_workspace()
    print(f"workspace: {workspace}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
    )
    meter = ToolMeterHook()
    harness.register_hook(meter)  # registration works after build, too
    recorder = EventRecorder()

    async with harness:
        await harness.run_task(
            intent="Create notes.txt containing the single line 'remember the milk'.",
            observer=recorder,
            max_sprints=3,
        )

    print("\n--- hook (inside the engine) ---")
    print(f"sessions seen: {meter.sessions}")
    for tool, count in sorted(meter.counts.items()):
        print(f"  {tool}: {count} dispatch(es)")
    print("--- observer (runner macro events) ---")
    print(f"event kinds: {sorted(set(recorder.kinds))}")


if __name__ == "__main__":
    asyncio.run(main())
