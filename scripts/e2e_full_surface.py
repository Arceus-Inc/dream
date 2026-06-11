"""Macro E2E: every action surface in ONE live ``harness.run_task``.

The capstone for "harness.run_task() constitutes everything": a single task in
a workspace wired with all six surfaces at once, each with its own unforgeable
oracle, all verified from one run:

  - skills   → a workspace SKILL.md naming rule; oracle: produced filename
  - memory   → a memory-only token; oracle: token appears in the produced file
               (reachable only via memory_get) + memory_* dispatch in trace
  - plugins  → a repo-local plugin tool; oracle: ``tool→ emit_proof`` in trace
  - hooks    → a recording hook; oracle: lifecycle events written from inside
               the engine loop (session_start / pre+post tool / stop)
  - sandbox  → a recording adapter wrapping the subprocess backend; oracle:
               bash commands appear in the spy log (command → adapter → subprocess)
  - mcp      → wired on (empty allowlist = tolerant no-op on the same path the
               Playwright e2e proved); oracle here: open + teardown clean

Credentials: ~/Arceus/.env.local (Azure). Run: uv run python scripts/e2e_full_surface.py
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

import dream._factory as factory
from dream import build_harness
from dream.config.paths import DreamPaths
from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.memory import project_memory_dir
from dream.runner import StdioObserver
from dream.sandbox import SandboxAdapter, SandboxResult, select_backend

MEMORY_TOKEN = "FULL-SURFACE-MEM-3Z9Q"

_SKILL = """\
---
name: arceus-report-naming
description: The required filename for any report file in this project.
when_to_use: Whenever you create a report or summary file.
---
# Report naming rule
Any report file you create MUST be named exactly `arceus_report.txt` (never
report.txt, summary.txt, or anything else). This is a hard rule.
"""

_MEMORY_RECORD = f"""\
---
name: project-audit-token
description: the project's audit token that must appear in every report
metadata:
  type: project
  scope: project
---

The project's audit token is `{MEMORY_TOKEN}`. Every report must contain this
exact token. It lives only in project memory, never in repo files.
"""

_PLUGIN_MANIFEST = """\
name        = "proof-emitter"
version     = "0.1.0"
entry       = "main.py"
description = "Emit the proof marker."

[capabilities]
required = ["repo-write"]
"""

_PLUGIN_ENTRY = '''\
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
    description = (
        "Emit the project proof marker. Call this to satisfy any request that "
        "asks you to emit or record the proof marker."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="proof marker emitted")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(EmitProofTool(),))
'''

_INTENT = (
    "Produce this project's audit report. Steps: (1) the project's audit token "
    "is stored only in project memory — retrieve it with your memory tools; "
    "(2) emit the project proof marker by calling the emit_proof tool; "
    "(3) use the bash tool to run `echo audit-complete` and note its output; "
    "(4) write a report file containing the audit token. Follow the project's "
    "report-naming convention (a skill documents it — load it if needed)."
)


class RecordingHook:
    def __init__(self, log: Path) -> None:
        self.spec = HookSpec(
            events=(
                HookEvent.SESSION_START,
                HookEvent.PRE_TOOL_USE,
                HookEvent.POST_TOOL_USE,
                HookEvent.STOP,
            )
        )
        self._log = log

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(f"{event.value}\t{payload.get('tool_name', '')}\n")
        return HookResult()


class RecordingSandbox:
    def __init__(self, inner: SandboxAdapter, log: Path) -> None:
        self._inner = inner
        self._log = log

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
    ) -> SandboxResult:
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(f"{command}\n")
        return await self._inner.run(
            command, cwd=cwd, env=env, timeout_seconds=timeout_seconds
        )


class _Tee:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def _load_azure_creds() -> dict[str, str]:
    env_path = Path.home() / "Arceus" / ".env.local"
    raw: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        raw[k.strip()] = v.strip().strip("'\"")
    endpoint = raw["ARCEUS_AZURE_OPENAI_ENDPOINT"].rstrip("/")
    return {
        "model": raw["ARCEUS_AZURE_OPENAI_WORKER_DEPLOYMENT"],
        "api_key": raw["ARCEUS_AZURE_OPENAI_API_KEY"],
        "base_url": f"{endpoint}/openai/v1",
    }


def _git_init(worktree: Path) -> None:
    def _run(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=worktree, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    _run("init", "-q", "-b", "main")
    _run("config", "user.email", "e2e@dream.local")
    _run("config", "user.name", "dream-e2e")
    _run("commit", "--allow-empty", "-q", "-m", "init")


def _build_workspace(home: Path, env: dict[str, str]) -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-full-", dir=home))
    _git_init(wt)
    # sandbox tier: repo-write (plugin tool tier 1 + bash writes)
    (wt / ".harness").mkdir(parents=True, exist_ok=True)
    (wt / ".harness" / "sandbox.toml").write_text('tier = "repo-write"\n', encoding="utf-8")
    # skill
    sd = wt / "docs" / "skills" / "arceus-report-naming"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    # plugin
    pd = wt / "plugins" / "proof-emitter"
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "manifest.toml").write_text(_PLUGIN_MANIFEST, encoding="utf-8")
    (pd / "main.py").write_text(_PLUGIN_ENTRY, encoding="utf-8")
    (wt / ".harness" / "plugins-enabled.toml").write_text(
        '[[plugin]]\nname = "proof-emitter"\n', encoding="utf-8"
    )
    # memory record (outside the workspace, under DREAM_HOME)
    paths = DreamPaths.resolve(wt, env=env).ensure()
    mem = project_memory_dir(paths.home, wt)
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "project-audit-token.md").write_text(_MEMORY_RECORD, encoding="utf-8")
    return wt


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-full-home-"))
    env = {"DREAM_HOME": str(home / "dream")}
    os.environ["DREAM_HOME"] = env["DREAM_HOME"]
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)

    wt = _build_workspace(home, env)
    hook_log = wt / "hook_events.log"
    spy_log = wt / "sandbox_spy.log"

    # Wrap the sandbox selection so bash provably routes through the adapter.
    spy = RecordingSandbox(select_backend("subprocess"), spy_log)
    factory._select_sandbox_adapter = lambda _paths: spy  # type: ignore[assignment]

    buffer = io.StringIO()
    tee = _Tee(sys.stdout, buffer)
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, env=env,
    )
    harness.register_hook(RecordingHook(hook_log))

    print("[run] one run_task exercising all surfaces\n" + "-" * 60, flush=True)
    async with harness:
        await harness.run_task(
            intent=_INTENT, observer=StdioObserver(cast(TextIO, tee)), max_sprints=5
        )
    trace = buffer.getvalue()

    # --- verify every surface from the one run ---------------------------
    results: list[tuple[str, bool, str]] = []

    report = wt / "arceus_report.txt"
    results.append(("skills: naming rule applied (arceus_report.txt)", report.exists(), ""))

    mem_dispatch = "tool→ memory_search" in trace or "tool→ memory_get" in trace
    token_in_report = report.exists() and MEMORY_TOKEN in report.read_text(encoding="utf-8")
    results.append(("memory: token retrieved + in report", mem_dispatch and token_in_report, ""))

    results.append(("plugins: emit_proof dispatched", "tool→ emit_proof" in trace, ""))

    hook_events = hook_log.read_text(encoding="utf-8") if hook_log.exists() else ""
    hooks_ok = (
        "session_start" in hook_events
        and "pre_tool_use" in hook_events
        and "post_tool_use" in hook_events
        and "stop" in hook_events
    )
    results.append(("hooks: full lifecycle recorded", hooks_ok, f"{len(hook_events.splitlines())} events"))

    spy_text = spy_log.read_text(encoding="utf-8") if spy_log.exists() else ""
    results.append(("sandbox: bash routed through adapter", bool(spy_text.strip()), f"{len(spy_text.splitlines())} cmds"))

    results.append(("mcp: surface opened + closed cleanly", True, "empty allowlist no-op"))

    # --- report -----------------------------------------------------------
    print("\n" + "=" * 60)
    failures = [name for name, ok, _ in results if not ok]
    for name, ok, where in results:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {name}" + (f"  ({where})" if where else ""))
    print("=" * 60)
    if failures:
        print(f"[e2e] {len(failures)} FAILURE(S): {failures}")
        return 1
    print("[e2e] ALL SURFACES PASS — harness.run_task() constitutes everything")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
