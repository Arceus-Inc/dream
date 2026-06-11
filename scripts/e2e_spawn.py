"""E2E: spawn_subagent through ``harness.run_task`` against the live model.

Proves the v1 spawn surface end to end: a parent task that MUST delegate via
``spawn_subagent`` — children summarize files into artifacts, the parent
merges. Oracles are unforgeable: spawn dispatches in the rendered trace,
child-session streaming present, the SUBAGENT_STOP hook fired from inside the
harness, child artifacts on disk, and a ``spawn=False`` control where the
tool cannot be dispatched because it is not in the schema.

Scenarios:
  1. (cheap)  spawn_subagent in a default session's wire schema; absent with
              spawn=False
  2. LIVE     delegation: >= 2 ``tool→ spawn_subagent`` dispatches + summary
              artifacts written by children
  3. (same)   children streamed into the transcript ([subagent] lines) and NO
              child ever dispatched spawn_subagent (depth-1 star holds)
  4. (same)   SUBAGENT_STOP hook fired once per completed child
  5. LIVE     control (spawn=False): task completes, zero spawn dispatches

Credentials: ~/Arceus/.env.local (Azure). Run: uv run python scripts/e2e_spawn.py
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, TextIO, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from dream import build_harness
from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.runner import StdioObserver
from dream.session import SessionOptions
from dream.tools.builtin import default_registry

_NOTES = {
    "alpha.txt": "The alpha system handles ingestion. It retries failed batches twice.",
    "beta.txt": "The beta system renders reports. It caches templates for one hour.",
    "gamma.txt": "The gamma system routes alerts. It deduplicates within five minutes.",
}

_INTENT = (
    "This workspace has three text files under notes/. You MUST delegate the "
    "reading: for EACH of the three files, call the spawn_subagent tool once, "
    "with a task telling the child to read that one file (read_file) and write "
    "a one-sentence summary to summaries/<filename>.summary.txt (write_file). "
    "Give each child the tools ['read_file', 'write_file']. Do NOT read the "
    "notes/ files yourself — only the children may read them. After all three "
    "children finish, write MERGED.md at the repo root combining the three "
    "summaries you received back."
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


class StopRecorder:
    """Counts SUBAGENT_STOP firings — proof children completed via the harness."""

    def __init__(self) -> None:
        self.spec = HookSpec(events=(HookEvent.SUBAGENT_STOP,))
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        self.payloads.append(dict(payload))
        return HookResult()


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


def _fresh_workspace(home: Path) -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-spawn-", dir=home))
    _git_init(wt)
    cfg = wt / ".harness" / "sandbox.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('tier = "repo-write"\n', encoding="utf-8")
    notes = wt / "notes"
    notes.mkdir()
    for name, body in _NOTES.items():
        (notes / name).write_text(body, encoding="utf-8")
    return wt


def _spawn_context_present(creds: dict[str, str], wt: Path, *, spawn: bool) -> bool:
    """Build a probe session and report whether the spawn context is stashed."""
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, mcp=False, plugins=False, spawn=spawn,
    )
    engine = harness.config._engine_factory("probe", SessionOptions())  # type: ignore[misc]
    metadata = engine.dispatcher.context_metadata  # type: ignore[union-attr]
    return "spawn_context" in metadata


async def _run(creds: dict[str, str], wt: Path, *, spawn: bool, hook: StopRecorder | None) -> str:
    buffer = io.StringIO()
    tee = _Tee(sys.stdout, buffer)
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, mcp=False, plugins=False, spawn=spawn,
    )
    if hook is not None:
        harness.register_hook(hook)
    async with harness:
        await harness.run_task(
            intent=_INTENT, observer=StdioObserver(cast(TextIO, tee)), max_sprints=4
        )
    return buffer.getvalue()


def _summaries(wt: Path) -> list[str]:
    d = wt / "summaries"
    return sorted(p.name for p in d.glob("*.summary.txt")) if d.exists() else []


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-spawn-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    # --- scenario 1: cheap wiring checks (no LLM) -------------------------
    wt1 = _fresh_workspace(home)
    tool_registered = "spawn_subagent" in {t.name for t in default_registry().list_tools()}
    ctx_on = _spawn_context_present(creds, wt1, spawn=True)
    ctx_off = _spawn_context_present(creds, wt1, spawn=False)
    results.append(
        ("1 wiring: tool registered; context on/off follows spawn flag",
         tool_registered and ctx_on and not ctx_off, "")
    )

    # --- scenarios 2-4: LIVE delegation -----------------------------------
    print("\n[scenario 2-4] LIVE: parent must delegate via spawn_subagent\n" + "-" * 60)
    wt2 = _fresh_workspace(home)
    hook = StopRecorder()
    trace = await _run(creds, wt2, spawn=True, hook=hook)

    dispatches = trace.count("tool→ spawn_subagent")
    found = _summaries(wt2)
    results.append(
        ("2 delegation: >=2 spawn dispatches + child artifacts",
         dispatches >= 2 and len(found) >= 2, f"{dispatches} dispatches, {found}")
    )

    child_streamed = "[subagent]" in trace
    child_spawned = "[subagent] tool→ spawn_subagent" in trace
    results.append(
        ("3 children streamed; no grandchild spawn (depth-1 holds)",
         child_streamed and not child_spawned, "")
    )

    completed_stops = [p for p in hook.payloads if p.get("status") == "completed"]
    results.append(
        ("4 SUBAGENT_STOP fired per completed child",
         len(completed_stops) >= 2, f"{len(hook.payloads)} firings")
    )

    # --- scenario 5: LIVE control (spawn=False) ----------------------------
    print("\n[scenario 5] LIVE: control spawn=False\n" + "-" * 60)
    wt5 = _fresh_workspace(home)
    trace5 = await _run(creds, wt5, spawn=False, hook=None)
    results.append(
        ("5 control: zero spawn dispatches", "tool→ spawn_subagent" not in trace5, "")
    )

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
    print("[e2e] all 5 scenarios PASS — spawn_subagent verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
