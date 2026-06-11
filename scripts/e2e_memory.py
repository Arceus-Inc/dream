"""E2E: memory read tools through ``harness.run_task`` against the live model.

Proves the Phase-2 wiring end to end. Workspace memory lives under
``~/.dream/memory/{project}-{sha}/`` — *outside* the workspace the agent is
confined to — so a secret stored there is reachable ONLY through the
``memory_search`` / ``memory_get`` tools, never with ``read_file`` or ``bash``.
That makes secret retrieval an unforgeable oracle: if the agent writes the
secret into a workspace file, it must have gone through memory.

Scenarios (cheap catalogue checks + live ``run_task`` runs):
  1. record present          → its description appears in the system prompt
  2. memory=False            → description ABSENT from the prompt
  3. tools registered        → memory_search + memory_get are in the registry
  4. LIVE secret retrieval   → run_task → canary.txt holds the memory-only token
                               AND the trace shows a memory_* dispatch
  5. LIVE control (mem off)  → run_task → token NOT obtained (store unwired)
  6. LIVE second secret      → a different memory-only token retrieved (robustness)

Credentials: ~/Arceus/.env.local (Azure). Run: uv run python scripts/e2e_memory.py
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

from dream import SessionOptions, build_harness
from dream.config.paths import DreamPaths
from dream.memory import project_memory_dir
from dream.runner import StdioObserver
from dream.tools.builtin import default_registry

# Two secrets only obtainable from the memory store (never written in-workspace).
CANARY = "CANARY-QX7731"
DEPLOY_KEY = "DEPLOY-KMARU-44"

_CANARY_RECORD = f"""\
---
name: project-canary-id
description: the project's canary identifier required before any deploy
metadata:
  type: project
  scope: project
---

This project's canary identifier is `{CANARY}`. Any deploy or release step
must reference this exact canary id. It is not written anywhere in the repo;
it lives only in project memory.
"""

_DEPLOY_RECORD = f"""\
---
name: deploy-key
description: the project's required deploy key string
metadata:
  type: project
  scope: project
---

The deploy key for this project is `{DEPLOY_KEY}`. It is stored only in memory.
"""


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


def _fresh_workspace(home: Path, env: dict[str, str], *records: tuple[str, str]) -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-mem-", dir=home))
    _git_init(wt)
    # Write the records into the project's memory dir (outside the workspace).
    paths = DreamPaths.resolve(wt, env=env).ensure()
    mem_dir = project_memory_dir(paths.home, wt)
    mem_dir.mkdir(parents=True, exist_ok=True)
    for slug, body in records:
        (mem_dir / f"{slug}.md").write_text(body, encoding="utf-8")
    return wt


def _system_prompt(creds: dict[str, str], wt: Path, env: dict[str, str], *, memory: bool) -> str:
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, memory=memory, env=env,
    )
    engine = harness.config._engine_factory("probe", SessionOptions())  # type: ignore[misc]
    return engine.streamer._system_prompt or ""  # type: ignore[union-attr]


async def _run_task_capture(
    creds: dict[str, str], wt: Path, env: dict[str, str], intent: str, *, memory: bool
) -> str:
    buffer = io.StringIO()
    tee = _Tee(sys.stdout, buffer)
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, memory=memory, env=env,
    )
    async with harness:
        await harness.run_task(
            intent=intent, observer=StdioObserver(cast(TextIO, tee)), max_sprints=4
        )
    return buffer.getvalue()


def _file_has(wt: Path, name: str, token: str) -> bool:
    f = wt / name
    return f.exists() and token in f.read_text(encoding="utf-8")


def _memory_dispatched(trace: str) -> bool:
    return "tool→ memory_search" in trace or "tool→ memory_get" in trace


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-mem-home-"))
    env = {"DREAM_HOME": str(home / "dream")}
    os.environ["DREAM_HOME"] = env["DREAM_HOME"]
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    # --- cheap catalogue checks (no LLM) ---------------------------------
    wt1 = _fresh_workspace(home, env, ("project-canary-id", _CANARY_RECORD))
    p_on = _system_prompt(creds, wt1, env, memory=True)
    results.append(("1 record description in prompt", "canary identifier" in p_on, ""))

    p_off = _system_prompt(creds, wt1, env, memory=False)
    results.append(("2 memory=False omits it", "canary identifier" not in p_off, ""))

    reg = default_registry()
    results.append(("3 memory tools registered", "memory_search" in reg and "memory_get" in reg, ""))

    # --- live run_task scenarios -----------------------------------------
    intent_canary = (
        "This project has a required canary identifier that is stored only in "
        "project memory (not in any repo file). Use your memory tools to find "
        "the project's canary identifier, then write exactly that identifier "
        "into a file named canary.txt in the working directory. Do not invent a "
        "value — retrieve the real one from memory."
    )
    print("\n[scenario 4] LIVE: memory secret retrieval → run_task\n" + "-" * 60)
    wt4 = _fresh_workspace(home, env, ("project-canary-id", _CANARY_RECORD))
    trace4 = await _run_task_capture(creds, wt4, env, intent_canary, memory=True)
    results.append((
        "4 canary retrieved via memory",
        _file_has(wt4, "canary.txt", CANARY) and _memory_dispatched(trace4),
        wt4.name,
    ))

    print("\n[scenario 5] LIVE: control (memory=False) → run_task\n" + "-" * 60)
    wt5 = _fresh_workspace(home, env, ("project-canary-id", _CANARY_RECORD))
    await _run_task_capture(creds, wt5, env, intent_canary, memory=False)
    results.append(("5 control: canary NOT obtained", not _file_has(wt5, "canary.txt", CANARY), wt5.name))

    intent_deploy = (
        "This project's deploy key is stored only in project memory. Use your "
        "memory tools to retrieve the deploy key and write exactly that string "
        "into deploy_key.txt in the working directory. Retrieve the real value."
    )
    print("\n[scenario 6] LIVE: second memory secret → run_task\n" + "-" * 60)
    wt6 = _fresh_workspace(home, env, ("deploy-key", _DEPLOY_RECORD))
    trace6 = await _run_task_capture(creds, wt6, env, intent_deploy, memory=True)
    results.append((
        "6 deploy key retrieved via memory",
        _file_has(wt6, "deploy_key.txt", DEPLOY_KEY) and _memory_dispatched(trace6),
        wt6.name,
    ))

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
    print("[e2e] all 6 scenarios PASS — memory read tools verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
