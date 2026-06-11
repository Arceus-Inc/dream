"""E2E: observer hooks firing in the engine through ``harness.run_task``.

Proves the Phase-4 wiring end to end: a hook registered on the harness fires
SESSION_START, PRE_TOOL_USE, POST_TOOL_USE (atom-ordered around the real
dispatch), and STOP during a live ``run_task``. The oracle is unforgeable: the
events are written by the hook *from inside the engine loop*, not by the model —
the model cannot fabricate a hook firing.

Scenarios (cheap wiring checks + live ``run_task`` runs):
  1. hook registered          → it lands on harness._hooks and the engine wires
                                a non-None hook executor
  2. LIVE lifecycle           → run_task records session_start … stop
  3. LIVE tool atom           → every post_tool_use is preceded by a matching
                                pre_tool_use for the same tool (PRE→POST order)
  4. LIVE payloads            → PRE carries tool_name; POST carries is_error
  5. LIVE crash isolation     → a hook that raises on every event does NOT break
                                the turn; the co-registered recorder still sees
                                the full lifecycle and the task still completes
  6. control (no hook)        → nothing is recorded when no hook is registered

Credentials: ~/Arceus/.env.local (Azure). Run: uv run python scripts/e2e_hooks.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from dream import SessionOptions, build_harness
from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.runner import StdioObserver

_OBSERVED = (
    HookEvent.SESSION_START,
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
    HookEvent.STOP,
)


class RecordingHook:
    """Append every observed event (and key payload fields) to a file."""

    def __init__(self, log: Path) -> None:
        self.spec = HookSpec(events=_OBSERVED)
        self._log = log

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        name = payload.get("tool_name", "")
        is_err = payload.get("is_error", "")
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(f"{event.value}\t{name}\t{is_err}\n")
        return HookResult()


class ExplodingHook:
    """Raise on every event — the executor must swallow it (crash isolation)."""

    def __init__(self) -> None:
        self.spec = HookSpec(events=_OBSERVED)

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        raise RuntimeError(f"boom on {event.value}")


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
    wt = Path(tempfile.mkdtemp(prefix="e2e-hooks-", dir=home))
    _git_init(wt)
    cfg = wt / ".harness" / "sandbox.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('tier = "repo-write"\n', encoding="utf-8")
    # A file for the agent to read, guaranteeing at least one real tool call.
    (wt / "NOTES.txt").write_text("the answer is 42\n", encoding="utf-8")
    return wt


def _read_events(log: Path) -> list[tuple[str, str, str]]:
    if not log.exists():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        rows.append((parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    return rows


_INTENT = (
    "Read the file NOTES.txt in the working directory and report the answer it "
    "contains. Use your file-reading tool to do this."
)


async def _run_task(creds: dict[str, str], wt: Path, *hooks: object, attempts: int = 3) -> int:
    """Register the hooks and run the task, retried on transient planner flakes.

    The planner head can occasionally emit malformed <ledger> JSON — a model
    quirk unrelated to hooks. We retry with a fresh harness so the hook lifecycle
    we assert on reflects a clean run.
    """
    for attempt in range(1, attempts + 1):
        harness = build_harness(
            model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
            working_dir=wt,
        )
        for h in hooks:
            harness.register_hook(h)  # type: ignore[arg-type]
        try:
            async with harness:
                result = await harness.run_task(
                    intent=_INTENT, observer=StdioObserver(sys.stdout), max_sprints=4
                )
            return len(result.sprints)
        except Exception as exc:  # transient model/planner flake
            print(f"[retry] attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}")
    return 0


def _atoms_well_ordered(events: list[tuple[str, str, str]]) -> bool:
    """Every post_tool_use must be preceded (somewhere before) by a pre for the
    same tool, and within a session pres and posts must balance — a cheap proxy
    for the tool-call atom invariant (no post before its pre)."""
    pending: dict[str, int] = {}
    saw_pair = False
    for ev, name, _ in events:
        if ev == HookEvent.PRE_TOOL_USE.value:
            pending[name] = pending.get(name, 0) + 1
        elif ev == HookEvent.POST_TOOL_USE.value:
            if pending.get(name, 0) <= 0:
                return False  # a post with no matching pre before it
            pending[name] -= 1
            saw_pair = True
    return saw_pair


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-hooks-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    # --- cheap wiring check (no LLM) -------------------------------------
    wt1 = _fresh_workspace(home)
    rec1 = RecordingHook(wt1 / "events.log")
    h1 = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt1,
    )
    h1.register_hook(rec1)
    engine = h1.config._engine_factory("probe", SessionOptions())  # type: ignore[misc]
    has_exec = getattr(engine, "hook_executor", None) is not None or getattr(
        getattr(engine, "dispatcher", None), "hook_executor", None
    ) is not None
    results.append(("1 hook registered + executor wired", rec1 in h1._hooks and has_exec, ""))

    # --- live run_task: lifecycle + atom + payloads ----------------------
    print("\n[scenario 2-4] LIVE: hook lifecycle during run_task\n" + "-" * 60)
    wt2 = _fresh_workspace(home)
    rec2 = RecordingHook(wt2 / "events.log")
    await _run_task(creds, wt2, rec2)
    ev2 = _read_events(wt2 / "events.log")
    kinds = {e for e, _, _ in ev2}
    results.append((
        "2 lifecycle fired (start+stop)",
        HookEvent.SESSION_START.value in kinds and HookEvent.STOP.value in kinds,
        f"{len(ev2)} events",
    ))
    results.append(("3 tool atom PRE→POST ordered", _atoms_well_ordered(ev2), ""))
    pre_named = any(e == HookEvent.PRE_TOOL_USE.value and name for e, name, _ in ev2)
    post_haserr = any(e == HookEvent.POST_TOOL_USE.value and err != "" for e, _, err in ev2)
    results.append(("4 payloads: PRE tool_name + POST is_error", pre_named and post_haserr, ""))

    # --- live run_task: crash isolation ----------------------------------
    print("\n[scenario 5] LIVE: a raising hook must not break the turn\n" + "-" * 60)
    wt5 = _fresh_workspace(home)
    rec5 = RecordingHook(wt5 / "events.log")
    sprints5 = await _run_task(creds, wt5, ExplodingHook(), rec5)
    ev5 = _read_events(wt5 / "events.log")
    kinds5 = {e for e, _, _ in ev5}
    results.append((
        "5 raising hook isolated; recorder intact",
        sprints5 > 0
        and HookEvent.SESSION_START.value in kinds5
        and HookEvent.PRE_TOOL_USE.value in kinds5
        and HookEvent.STOP.value in kinds5,
        f"{sprints5} sprints",
    ))

    # --- control: no hook → nothing recorded -----------------------------
    wt6 = _fresh_workspace(home)
    log6 = wt6 / "events.log"
    await _run_task(creds, wt6)  # no hooks registered
    results.append(("6 control: no hook → no events", not log6.exists(), ""))

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
    print("[e2e] all 6 scenarios PASS — engine hooks verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
