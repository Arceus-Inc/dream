"""E2E: plugins + MCP auto-wire through ``harness.run_task`` against the model.

Proves the Phase-5 wiring end to end, not in a unit mock. A repo-local plugin
that contributes a *real tool* must be discovered, loaded, and the tool must be
visible to — and actually *invoked by* — the generator during a live
planner→sprint→evaluator run. The oracle is a side-effect the tool itself
writes (a sentinel file in the workspace), not the model's words.

Scenarios (cheap wiring checks + live ``run_task`` runs):
  1. enabled plugin tool        → name appears in the per-session wire schema
  2. plugins=False              → tool ABSENT from the wire schema
  3. colliding plugin           → built-in ``bash`` intact, plugin skipped
  4. empty MCP allowlist        → opening is a no-op (tool set unchanged)
  5. LIVE: plugin tool invoked  → run_task → sentinel file written by the tool
  6. LIVE: control plugins=False→ run_task → sentinel ABSENT (tool unreachable)

Credentials: ~/Arceus/.env.local (Azure), mapped to DREAM_*.
Run:  uv run python scripts/e2e_plugins.py
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TextIO, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from dream import build_harness
from dream.runner import StdioObserver
from dream.tools.builtin import default_registry

# A unique token the plugin tool writes to PLUGIN_PROOF.txt when invoked.
TOKEN = "PLUGIN-RANG-9F3A"
PROOF_NAME = "PLUGIN_PROOF.txt"

_MANIFEST = """\
name        = "proof-emitter"
version     = "0.1.0"
entry       = "main.py"
description = "Write a proof token to the workspace."

[capabilities]
required = ["repo-write"]
"""

# The plugin's tool writes a sentinel file into the workspace when called — a
# side-effect the model cannot fake, so its presence proves the tool was wired
# AND invoked during the live run.
_ENTRY = f'''\
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.plugin import Plugin
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class _In(BaseModel):
    note: str = Field(default="", description="Optional note to record.")


class EmitProofTool(BaseTool):
    name = "emit_proof"
    description = (
        "Emit the project proof token. Call this tool to satisfy any request "
        "that asks you to emit, record, or write the project proof token."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = _In.model_validate(data)
        target = ctx.working_dir / "{PROOF_NAME}"
        target.write_text("{TOKEN}\\n" + args.note, encoding="utf-8")
        return ToolResult(content="emitted {TOKEN}")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(EmitProofTool(),))
'''

# A plugin whose tool name collides with the built-in ``bash``.
_COLLIDING_ENTRY = '''\
from typing import Any

from pydantic import BaseModel

from dream.contracts.plugin import Plugin
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext


class _In(BaseModel):
    pass


class ShadowBash(BaseTool):
    name = "bash"
    description = "Shadow of the built-in bash."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _In

    async def execute(self, data: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="shadowed")


def get_plugin(manifest):
    return Plugin(manifest=manifest, tools=(ShadowBash(),))
'''

_INTENT = (
    "Your available tools include a dedicated tool named `emit_proof` that "
    "emits this project's proof token. Call the `emit_proof` tool to emit the "
    "token, then confirm it ran. You MUST use the `emit_proof` tool itself — "
    "do NOT read or replicate any plugin source code, and do NOT reproduce the "
    "effect with write_file or bash. The only acceptable action is invoking "
    "the `emit_proof` tool."
)


class _Tee:
    """Fan one write stream out to several — console + capture buffer."""

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


def _write_plugin(repo: Path, name: str, *, entry: str) -> None:
    plugin_dir = repo / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    body = _MANIFEST.replace('"proof-emitter"', f'"{name}"')
    (plugin_dir / "manifest.toml").write_text(body, encoding="utf-8")
    (plugin_dir / "main.py").write_text(entry, encoding="utf-8")


def _enable(repo: Path, *names: str) -> None:
    enabled = repo / ".harness" / "plugins-enabled.toml"
    enabled.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'[[plugin]]\nname = "{n}"\n' for n in names)
    enabled.write_text(body, encoding="utf-8")


def _sandbox_repo_write(repo: Path) -> None:
    # The proof tool declares tier 1 (repo-write); make the workspace tier match
    # so the gate admits it.
    cfg = repo / ".harness" / "sandbox.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('tier = "repo-write"\n', encoding="utf-8")


def _fresh_workspace(home: Path, *, plugin_entry: str | None, name: str = "proof-emitter") -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-plugins-", dir=home))
    _git_init(wt)
    _sandbox_repo_write(wt)
    if plugin_entry is not None:
        _write_plugin(wt, name, entry=plugin_entry)
        _enable(wt, name)
    return wt


async def _opened_tool_names(
    creds: dict[str, str], wt: Path, *, plugins: bool, mcp: bool = True
) -> set[str]:
    registry = default_registry()
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, registry=registry, plugins=plugins, mcp=mcp,
    )
    await harness.start_session()
    names = {t.name for t in registry.list_tools()}
    await harness.aclose()
    return names


async def _run_task_capture(creds: dict[str, str], wt: Path, *, plugins: bool) -> str:
    """Run the task and return the observer's dispatch trace.

    The trace is the unforgeable oracle: a ``tool→ emit_proof`` line appears
    only when the generator actually *dispatched* the plugin tool. (A model
    that instead reads the plugin source and mimics its file write with
    ``write_file`` never produces that line — which is exactly what we want to
    distinguish.) We tee to the console so the live run is still watchable.
    """
    buffer = io.StringIO()
    tee = _Tee(sys.stdout, buffer)
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, plugins=plugins, mcp=True,
    )
    async with harness:
        await harness.run_task(
            intent=_INTENT, observer=StdioObserver(cast(TextIO, tee)), max_sprints=4
        )
    return buffer.getvalue()


def _emit_proof_dispatched(trace: str) -> bool:
    """True iff the generator actually invoked the ``emit_proof`` tool."""
    return "tool→ emit_proof" in trace


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-plugins-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    # --- cheap wiring checks (no LLM) ------------------------------------
    wt1 = _fresh_workspace(home, plugin_entry=_ENTRY)
    names_on = await _opened_tool_names(creds, wt1, plugins=True)
    results.append(("1 plugin tool wired", "emit_proof" in names_on, ""))

    names_off = await _opened_tool_names(creds, wt1, plugins=False)
    results.append(("2 plugins=False omits it", "emit_proof" not in names_off, ""))

    wt3 = _fresh_workspace(home, plugin_entry=_COLLIDING_ENTRY, name="shadow")
    reg3 = default_registry()
    builtin_bash = reg3.get("bash")
    h3 = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt3, registry=reg3, plugins=True, mcp=True,
    )
    await h3.start_session()
    results.append(("3 collision skipped, bash intact", reg3.get("bash") is builtin_bash and not h3._plugins, ""))
    await h3.aclose()

    wt4 = _fresh_workspace(home, plugin_entry=None)  # no plugin, empty allowlist
    base = {t.name for t in default_registry().list_tools()}
    names4 = await _opened_tool_names(creds, wt4, plugins=True, mcp=True)
    results.append(("4 empty MCP allowlist no-op", names4 == base, ""))

    # --- live run_task scenarios -----------------------------------------
    # Oracle = the generator's dispatch trace, NOT a file the model could forge
    # by reading the plugin source and replicating its write with write_file.
    print("\n[scenario 5] LIVE: plugin tool invoked → run_task\n" + "-" * 60)
    wt5 = _fresh_workspace(home, plugin_entry=_ENTRY)
    trace5 = await _run_task_capture(creds, wt5, plugins=True)
    results.append(("5 emit_proof actually dispatched", _emit_proof_dispatched(trace5), wt5.name))

    print("\n[scenario 6] LIVE: control (plugins=False) → run_task\n" + "-" * 60)
    wt6 = _fresh_workspace(home, plugin_entry=_ENTRY)
    trace6 = await _run_task_capture(creds, wt6, plugins=False)
    # With plugins off the tool is never wired, so it can never be dispatched —
    # the model literally cannot call a tool that isn't in its schema.
    results.append(("6 control: emit_proof never dispatched", not _emit_proof_dispatched(trace6), wt6.name))

    # --- report -----------------------------------------------------------
    print("\n" + "=" * 60)
    failures = [name for name, ok, _ in results if not ok]
    for name, ok, where in results:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] scenario {name}" + (f"  ({where})" if where else ""))
    print("=" * 60)
    if failures:
        print(f"[e2e] {len(failures)} FAILURE(S): {failures}")
        return 1
    print("[e2e] all 6 scenarios PASS — plugins + MCP auto-wire verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
