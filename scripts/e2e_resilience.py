"""E2E: run_task resilience — sprint adaptation live, against the real model.

Proves Fix 2 (notes carry-through + N-strikes) on the real planner→sprint loop:
a step that keeps getting ``needs-changes`` must escalate to ``blocked`` after
2 strikes instead of burning ``max_sprints``, with the evaluator's feedback
readable on the step.

To make escalation deterministic the evaluator-run head is injected (always
returns ``needs-changes`` with pointed notes) while the planner, negotiation
heads, and generator stay LIVE — so the loop, ledger persistence, escalation,
and observer events are exercised end to end against gpt-5.2. Fix 1 (head
retry) cannot be forced live — a real model can't be made to emit malformed
envelopes on demand — and is covered by 37 unit tests in
tests/test_runner/test_head_retry.py instead.

Scenarios:
  1. (cheap)  a ledger JSON written before this feature (no
              needs_changes_count key) loads with count 0 — compat
  2. LIVE     happy path: simple task completes; no sprint.escalated event
  3. LIVE     forced needs-changes evaluator: exactly 2 sprints used out of
              max_sprints=6 + sprint.escalated observed
  4. (same)   final ledger: step blocked with "[evaluator, sprint 1]" AND
              "[evaluator, sprint 2]" notes accumulated
  5. (same)   final ledger: step.needs_changes_count == 2

Credentials: ~/Arceus/.env.local (Azure). Run: uv run python scripts/e2e_resilience.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from dream import build_harness
from dream.planner import PlannerLedger
from dream.runner import StdioObserver
from dream.sprint._evaluation import EvaluationRecord


class RecordingObserver:
    """Delegate to StdioObserver while keeping every raw event for assertions."""

    def __init__(self) -> None:
        self._stdio = StdioObserver(sys.stdout)
        self.events: list[dict[str, Any]] = []

    def on_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self._stdio.on_event(event)

    def kinds(self) -> list[str]:
        return [e.get("kind", "") for e in self.events]


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
    wt = Path(tempfile.mkdtemp(prefix="e2e-resil-", dir=home))
    _git_init(wt)
    cfg = wt / ".harness" / "sandbox.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('tier = "repo-write"\n', encoding="utf-8")
    return wt


def _final_ledger(wt: Path) -> PlannerLedger | None:
    # The runner persists the ledger at the exec-plans convention
    # (docs/exec-plans/active/t-*.json), not under .dream/planner.
    active = wt / "docs" / "exec-plans" / "active"
    candidates = sorted(
        p for p in (active.glob("t-*.json") if active.exists() else [])
        if "-sprint-" not in p.name
    )
    if not candidates:
        return None
    return PlannerLedger.load(candidates[-1])


def _make_rejecting_evaluator() -> Any:
    """An evaluator-run head that always returns needs-changes with notes."""

    async def evaluator_run(
        task_id: str, sprint_number: int, contract: Any, step: Any
    ) -> EvaluationRecord:
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_number,
            step_id=step.id,
            outcome="needs-changes",
            score=0.0,
            notes=(
                "the produced artifact was rejected by policy; an operator "
                "must approve this step before it can pass"
            ),
        )

    return evaluator_run


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-resil-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    # --- scenario 1: pre-feature ledger JSON loads (compat, no LLM) -------
    wt1 = _fresh_workspace(home)
    legacy = {
        "task_id": "t-legacy",
        "intent": "x",
        "created_at": 0.0,
        "steps": [
            {"id": "s1", "description": "d", "status": "in_progress", "notes": ""}
        ],
        "evaluator_enabled": True,
    }
    legacy_path = wt1 / "legacy-ledger.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = PlannerLedger.load(legacy_path)
    results.append(
        ("1 legacy ledger loads, count 0", loaded.steps[0].needs_changes_count == 0, "")
    )

    # --- scenario 2: LIVE happy path — no escalation regression -----------
    print("\n[scenario 2] LIVE: happy path, real heads\n" + "-" * 60)
    wt2 = _fresh_workspace(home)
    obs2 = RecordingObserver()
    h2 = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt2,
    )
    async with h2:
        await h2.run_task(
            intent=(
                "Create a file named hello.txt containing the word 'hello' "
                "using the write_file tool, then confirm it exists."
            ),
            observer=obs2,
            max_sprints=4,
        )
    results.append(
        ("2 happy path: no sprint.escalated", "sprint.escalated" not in obs2.kinds(), "")
    )

    # --- scenario 3-5: LIVE forced needs-changes → escalation --------------
    print("\n[scenario 3-5] LIVE: rejecting evaluator → must escalate at 2\n" + "-" * 60)
    wt3 = _fresh_workspace(home)
    obs3 = RecordingObserver()
    h3 = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt3,
    )
    async with h3:
        result3 = await h3.run_task(
            intent=(
                "Create a file named greeting.txt containing the word 'hi' "
                "using the write_file tool."
            ),
            evaluator_run=_make_rejecting_evaluator(),
            observer=obs3,
            max_sprints=6,
        )
    sprints_used = len(result3.sprints)
    escalated = "sprint.escalated" in obs3.kinds()
    results.append(
        (
            "3 escalated after exactly 2 sprints (budget intact)",
            escalated and sprints_used == 2,
            f"{sprints_used} sprints of 6",
        )
    )

    ledger3 = _final_ledger(wt3)
    step3 = ledger3.steps[0] if ledger3 and ledger3.steps else None
    notes_ok = (
        step3 is not None
        and step3.status == "blocked"
        and "[evaluator, sprint 1]" in step3.notes
        and "[evaluator, sprint 2]" in step3.notes
    )
    results.append(("4 step blocked with both sprints' notes", notes_ok, ""))
    results.append(
        (
            "5 needs_changes_count == 2 on disk",
            step3 is not None and step3.needs_changes_count == 2,
            "",
        )
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
    print("[e2e] all 5 scenarios PASS — run_task resilience verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
