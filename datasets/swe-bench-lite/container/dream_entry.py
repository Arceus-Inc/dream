#!/usr/bin/env python3
"""Runs INSIDE the SWE-bench task container: dream solves the issue on /testbed.

dream operates on the repo at /testbed and evaluates against the REAL test suite
(the task's built conda env), so its plan -> sprint -> evaluate loop is faithful.
Token/sprint metrics are written to /out/result.json; the orchestrator extracts
the git diff from /testbed afterwards.

Env (set by the orchestrator):
  DREAM_SMOKE_API_KEY / DREAM_SMOKE_MODEL / DREAM_SMOKE_BASE_URL  model creds
  BENCH_TASK_JSON     path to the task record (json) inside the container
  BENCH_TESTBED_PY    path to the testbed python (runs the oracle tests)
  BENCH_MAX_SPRINTS   sprint cap
  BENCH_MAX_TURNS     per-sprint tool-call turn cap
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

TESTBED = Path("/testbed")
OUT = Path("/out/result.json")


def build_intent(task: dict, test_cmd: str) -> str:
    return (
        f"You are fixing a real bug in the `{task['repo']}` repository, already checked out at "
        f"/testbed at the buggy commit.\n\n"
        f"ISSUE\n-----\n{task['problem_statement']}\n\n"
        f"TASK\n----\n"
        f"Edit the library SOURCE files under /testbed to resolve the issue. The acceptance check "
        f"is this exact command (run it to verify your fix):\n\n    {test_cmd}\n\n"
        f"It must exit 0 (all listed tests pass). Do not weaken or delete unrelated tests; keep the "
        f"change minimal and do not break unrelated behavior."
    )


async def main() -> int:
    task = json.loads(Path(os.environ["BENCH_TASK_JSON"]).read_text(encoding="utf-8"))
    testbed_py = os.environ.get("BENCH_TESTBED_PY", "python")
    max_sprints = int(os.environ.get("BENCH_MAX_SPRINTS", "6"))
    max_turns = int(os.environ.get("BENCH_MAX_TURNS", "12"))

    f2p = task["FAIL_TO_PASS"]
    test_cmd = f"{testbed_py} -m pytest -p no:cacheprovider -q " + " ".join(f2p)

    env = {
        "DREAM_SMOKE_API_KEY": os.environ["DREAM_SMOKE_API_KEY"],
        "DREAM_SMOKE_MODEL": os.environ["DREAM_SMOKE_MODEL"],
        "DREAM_SMOKE_BASE_URL": os.environ["DREAM_SMOKE_BASE_URL"],
    }

    (TESTBED / ".harness").mkdir(parents=True, exist_ok=True)
    (TESTBED / ".harness" / "sandbox.toml").write_text(
        'tier = "unrestricted"\nconfirm_unrestricted = true\n', encoding="utf-8"
    )

    from dream.repl._session import build_default_harness

    result_meta: dict = {"instance_id": task["instance_id"], "harness": "dream", "model": env["DREAM_SMOKE_MODEL"]}
    t0 = time.perf_counter()
    try:
        harness = build_default_harness(env=env, working_dir=TESTBED, max_turns=max_turns)
        async with harness:
            result = await harness.run_task(
                intent=build_intent(task, test_cmd),
                worktree_root=TESTBED,
                verification_steps=({"kind": "test", "command": test_cmd},),
                max_sprints=max_sprints,
            )
        usage = result.usage_by_model
        it = sum(u.input_tokens for u in usage.values())
        ot = sum(u.output_tokens for u in usage.values())
        outcomes = []
        for s in result.sprints:
            v = getattr(s, "verdict", None) or getattr(s, "outcome", None) or getattr(s, "status", None)
            outcomes.append(str(v))
        result_meta.update(
            ok=True,
            error=None,
            input_tokens=it,
            output_tokens=ot,
            total_tokens=it + ot,
            models=sorted(usage),
            sprints=len(result.sprints),
            sprint_outcomes=outcomes,
            steps_total=len(result.final_ledger.steps),
            steps_done=sum(s.status == "done" for s in result.final_ledger.steps),
        )
    except Exception as exc:  # noqa: BLE001
        result_meta.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:600],
                           input_tokens=0, output_tokens=0, total_tokens=0)
    result_meta["seconds"] = round(time.perf_counter() - t0, 1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result_meta, ensure_ascii=False), encoding="utf-8")
    print(f"[dream_entry] {result_meta.get('ok')} sprints={result_meta.get('sprints')} "
          f"tokens={result_meta.get('total_tokens')} {result_meta['seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
