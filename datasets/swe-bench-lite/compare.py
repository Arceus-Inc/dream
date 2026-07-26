#!/usr/bin/env python3
"""Aggregate dream vs opencode results into a comparison report.

Reads each harness's ``results/<h>/metrics.jsonl`` (per-task tokens/time/patch) and the official
grading report ``results/<h>/*.<run_id>.json`` (resolved_ids), and emits a markdown + json
comparison: resolve rate, tokens, wall time, cost, steps, empty-patch/error rates, and a per-task
table.

    python compare.py --run-id full1
"""

from __future__ import annotations

import argparse
import json
import statistics as stats
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
HARNESSES = ("dream", "opencode")


def load_metrics(h: str) -> dict[str, dict]:
    p = RESULTS / h / "metrics.jsonl"
    out: dict[str, dict] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["instance_id"]] = r
    return out


def load_resolved(h: str) -> tuple[set[str], Path | None]:
    reports = sorted((RESULTS / h).glob("*.json"))
    # Grading reports have 'resolved_ids'; metrics.jsonl is not .json so safe.
    for rep in reversed(reports):
        try:
            d = json.loads(rep.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "resolved_ids" in d:
            return set(d.get("resolved_ids", [])), rep
    return set(), None


def agg(nums: list[float]) -> dict:
    nums = [n for n in nums if n is not None]
    if not nums:
        return {"mean": 0, "median": 0, "sum": 0, "n": 0}
    return {"mean": round(stats.mean(nums), 1), "median": round(stats.median(nums), 1),
            "sum": round(sum(nums), 1), "n": len(nums)}


def summarize(h: str) -> dict:
    m = load_metrics(h)
    resolved, rep = load_resolved(h)
    ids = sorted(m)
    n = len(ids)
    ok = [i for i in ids if m[i].get("ok")]
    nonempty = [i for i in ids if not m[i].get("empty_patch", True)]
    res = [i for i in ids if i in resolved]
    return {
        "harness": h,
        "tasks": n,
        "resolved": len(res),
        "resolve_rate": round(100 * len(res) / n, 1) if n else 0,
        "ran_ok": len(ok),
        "errors": n - len(ok),
        "nonempty_patch": len(nonempty),
        "empty_patch": n - len(nonempty),
        "tokens": agg([m[i].get("total_tokens", 0) for i in ids]),
        "input_tokens": agg([m[i].get("input_tokens", 0) for i in ids]),
        "output_tokens": agg([m[i].get("output_tokens", 0) for i in ids]),
        "wall_seconds": agg([m[i].get("wall_seconds", m[i].get("seconds", 0)) for i in ids]),
        "steps": agg([m[i].get("steps", m[i].get("sprints", 0)) for i in ids]),
        "cost_reported": agg([m[i].get("cost", 0) for i in ids]),
        "resolved_ids": sorted(res),
        "report": rep.name if rep else None,
        "_metrics": m,
        "_resolved": resolved,
    }


def md_table(rows: list[list[str]], header: list[str]) -> str:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run")
    args = ap.parse_args()

    summaries = {h: summarize(h) for h in HARNESSES}
    d, o = summaries["dream"], summaries["opencode"]

    # Union of instance ids across both harnesses, task order from tasks.jsonl.
    task_order = [json.loads(l)["instance_id"]
                  for l in (HERE / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    all_ids = [i for i in task_order if i in d["_metrics"] or i in o["_metrics"]]

    # Headline table.
    def row(s: dict) -> list[str]:
        return [s["harness"], f'{s["resolved"]}/{s["tasks"]} ({s["resolve_rate"]}%)',
                f'{s["tokens"]["mean"]:.0f}', f'{s["input_tokens"]["mean"]:.0f}',
                f'{s["output_tokens"]["mean"]:.0f}', f'{s["wall_seconds"]["mean"]:.0f}s',
                f'{s["steps"]["mean"]:.1f}', s["empty_patch"], s["errors"]]

    headline = md_table(
        [row(d), row(o)],
        ["harness", "resolved", "avg tokens", "avg in", "avg out", "avg time", "avg steps", "empty", "errors"],
    )

    # Per-task table.
    per_rows = []
    for i in all_ids:
        dm, om = d["_metrics"].get(i, {}), o["_metrics"].get(i, {})
        per_rows.append([
            i,
            "✓" if i in d["_resolved"] else ("·" if i in d["_metrics"] else "—"),
            "✓" if i in o["_resolved"] else ("·" if i in o["_metrics"] else "—"),
            dm.get("total_tokens", "—"), om.get("total_tokens", "—"),
            f'{dm.get("wall_seconds", dm.get("seconds", "—"))}', f'{om.get("wall_seconds", "—")}',
        ])
    per_task = md_table(
        per_rows,
        ["instance", "dream", "opencode", "dream tok", "oc tok", "dream s", "oc s"],
    )

    md = f"""# dream vs opencode — SWE-bench Lite ({len(all_ids)} tasks)

Model: **gpt-5.2** (same endpoint for both) · in-container (real test env) · official SWE-bench Docker grading.

## Headline

{headline}

- **Resolve rate** = official SWE-bench resolution (FAIL_TO_PASS flip + PASS_TO_PASS hold).
- **avg tokens** = mean total tokens/task. dream total = input+output; opencode total includes reasoning + cached-context reads (per-turn billed). Token accounting differs slightly between harnesses — treat as indicative.
- `·` ran but not resolved · `—` not attempted · `✓` resolved.

## Per-task

{per_task}

## Notes
- dream: plan → sprint → evaluate loop, verification = FAIL_TO_PASS pytest in the testbed env.
- opencode: `run --format json` non-interactive, same prompt + acceptance command.
- Oracle-assisted: both get the acceptance test command; test files excluded from graded patch; canonical grading re-applies the pristine test_patch.
"""
    out_md = HERE / "COMPARISON.md"
    out_md.write_text(md, encoding="utf-8")
    out_json = HERE / "comparison.json"
    clean = {h: {k: v for k, v in s.items() if not k.startswith("_")} for h, s in summaries.items()}
    out_json.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    print(md)
    print(f"\n[compare] wrote {out_md.name} and {out_json.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
