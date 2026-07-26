#!/usr/bin/env python3
"""Select a stratified subset of SWE-bench Lite for the dream-vs-opencode benchmark.

Downloads ``princeton-nlp/SWE-bench_Lite`` (test split, 300 real GitHub issues) and
writes ``tasks.jsonl`` with a diverse, deterministic sample (per-repo cap so django
does not dominate). Each record carries task + gold fix + test oracle so the same
file drives agent runs AND official Docker grading.

    uv run --with datasets python datasets/swe-bench-lite/build_tasks.py --n 25
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "tasks.jsonl"

# Fields we persist (superset of what the agent runner and grader need).
FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "environment_setup_commit",
    "version",
    "created_at",
    "problem_statement",
    "hints_text",
    "patch",  # gold fix (upper bound / grading reference)
    "test_patch",  # adds the regression tests
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)


def _as_list(v: object) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        try:
            return [str(x) for x in json.loads(v)]
        except json.JSONDecodeError:
            return [v]
    return []


def select(rows: list[dict], n: int, per_repo_cap: int, seed: int) -> list[dict]:
    """Deterministic round-robin across repos, capped, targeting ``n`` tasks."""
    import random

    rng = random.Random(seed)
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    for repo in by_repo:
        by_repo[repo].sort(key=lambda r: r["instance_id"])
        rng.shuffle(by_repo[repo])

    repos = sorted(by_repo, key=lambda k: (-len(by_repo[k]), k))
    picked: list[dict] = []
    taken: dict[str, int] = defaultdict(int)
    # Round-robin one task per repo per pass until we hit n or exhaust the cap.
    while len(picked) < n:
        progressed = False
        for repo in repos:
            if len(picked) >= n:
                break
            if taken[repo] >= per_repo_cap:
                continue
            idx = taken[repo]
            if idx < len(by_repo[repo]):
                picked.append(by_repo[repo][idx])
                taken[repo] += 1
                progressed = True
        if not progressed:
            break
    return picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25, help="number of tasks to select")
    ap.add_argument("--per-repo-cap", type=int, default=4, help="max tasks per repo")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    from datasets import load_dataset

    print("[build] loading princeton-nlp/SWE-bench_Lite (test) …", flush=True)
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    rows = [dict(r) for r in ds]
    print(f"[build] {len(rows)} tasks in the full Lite test split", flush=True)

    picked = select(rows, args.n, args.per_repo_cap, args.seed)

    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        for r in picked:
            rec = {k: r.get(k) for k in FIELDS}
            rec["FAIL_TO_PASS"] = _as_list(r.get("FAIL_TO_PASS"))
            rec["PASS_TO_PASS"] = _as_list(r.get("PASS_TO_PASS"))
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    dist: dict[str, int] = defaultdict(int)
    for r in picked:
        dist[r["repo"]] += 1
    print(f"[build] wrote {len(picked)} tasks -> {OUT}")
    for repo, c in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {repo:32s} {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
