#!/usr/bin/env python3
"""Detailed per-harness stats: time, tokens, cost, steps, patch size — split by graded outcome.

Joins ``results/<h>/metrics.jsonl`` with a SWE-bench grading report (``*.json`` containing
``resolved_ids``) found in results/<h>/ or a directory given with --reports.

    python stats.py --reports .debug
"""

from __future__ import annotations

import argparse
import json
import statistics as st
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


def load_report(h: str, extra: Path | None) -> tuple[set[str], set[str], str]:
    """Return (resolved_ids, submitted_ids, report_name) for the widest-coverage report."""
    cands = list((RESULTS / h).glob("*.json"))
    if extra:
        cands += list(extra.glob(f"{h}.*.json"))
    best: tuple[set[str], set[str], str] = (set(), set(), "none")
    for rep in cands:
        try:
            d = json.loads(rep.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "resolved_ids" in d:
            sub = set(d.get("submitted_ids", []))
            if len(sub) >= len(best[1]):
                best = (set(d["resolved_ids"]), sub, rep.name)
    return best


def five(vals: list[float]) -> str:
    v = [x for x in vals if x is not None]
    if not v:
        return "—"
    v.sort()
    return (f"mean {st.mean(v):,.0f} · med {st.median(v):,.0f} · "
            f"min {v[0]:,.0f} · max {v[-1]:,.0f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default="", help="extra dir holding grading reports")
    args = ap.parse_args()
    extra = Path(args.reports).resolve() if args.reports else None
    if extra and not extra.is_absolute():
        extra = HERE / args.reports

    data = {}
    for h in HARNESSES:
        m = load_metrics(h)
        resolved, submitted, repname = load_report(h, extra)
        data[h] = (m, resolved, submitted, repname)

    # Only compare on tasks BOTH harnesses attempted, for apples-to-apples.
    common = set(data["dream"][0]) & set(data["opencode"][0])
    # Resolve rates only over tasks BOTH harnesses had GRADED.
    graded = common & data["dream"][2] & data["opencode"][2]
    ungraded = sorted(common - graded)

    print(f"\n{'='*78}\nDETAILED STATS\n{'='*78}")
    print(f"attempted by both: {len(common)}   ·   graded for both: {len(graded)}")
    if ungraded:
        print(f"NOT YET GRADED ({len(ungraded)}): {', '.join(ungraded)}")

    for h in HARNESSES:
        m, resolved, submitted, repname = data[h]
        ids = sorted(common)          # timing/token stats over everything attempted
        gids = sorted(graded)         # accuracy stats only over graded
        secs = [m[i].get("wall_seconds", 0) for i in ids]
        toks = [m[i].get("total_tokens", 0) for i in ids]
        ins = [m[i].get("input_tokens", 0) for i in ids]
        outs = [m[i].get("output_tokens", 0) for i in ids]
        steps = [m[i].get("steps", m[i].get("sprints", 0)) for i in ids]
        plen = [m[i].get("patch_len", 0) for i in ids]
        costs = [m[i].get("cost", 0) or 0 for i in ids]
        res = [i for i in gids if i in resolved]
        unres = [i for i in gids if i not in resolved]

        print(f"\n### {h}   (report: {repname})")
        print(f"  resolved        : {len(res)}/{len(gids)}  ({100*len(res)/max(len(gids),1):.0f}%)   [graded only]")
        print(f"  time (s)        : {five(secs)}")
        print(f"  total tokens    : {five(toks)}")
        print(f"  input tokens    : {five(ins)}")
        print(f"  output tokens   : {five(outs)}")
        print(f"  steps/sprints   : {five(steps)}")
        print(f"  patch bytes     : {five(plen)}")
        print(f"  TOTAL time      : {sum(secs)/60:,.1f} min")
        print(f"  TOTAL tokens    : {sum(toks):,}")
        if sum(costs):
            print(f"  TOTAL cost      : ${sum(costs):,.2f}   (per resolved ${sum(costs)/max(len(res),1):,.3f})")
        print(f"  tokens/resolved : {sum(m[i].get('total_tokens',0) for i in gids)/max(len(res),1):,.0f}")
        print(f"  time/resolved   : {sum(m[i].get('wall_seconds',0) for i in gids)/max(len(res),1):,.0f}s")
        if res:
            print(f"  resolved  → time {five([m[i]['wall_seconds'] for i in res])}")
            print(f"             tokens {five([m[i]['total_tokens'] for i in res])}")
        if unres:
            print(f"  UNresolved→ time {five([m[i]['wall_seconds'] for i in unres])}")
            print(f"             tokens {five([m[i]['total_tokens'] for i in unres])}")
            print(f"             ids: {', '.join(unres)}")

    # Head-to-head per task.
    dm, dres, dsub, _ = data["dream"]
    om, ores, osub, _ = data["opencode"]
    print(f"\n{'='*78}\nPER-TASK  (✓ resolved · ✗ not · ? not yet graded)\n{'='*78}")
    hdr = f"{'instance':38} {'dream':>14}  {'opencode':>14}"
    print(hdr)
    print("-" * len(hdr))
    dwin = owin = 0
    for i in sorted(common):
        dok = "✓" if i in dres else ("✗" if i in dsub else "?")
        ook = "✓" if i in ores else ("✗" if i in osub else "?")
        if i in graded and dok != ook:
            dwin += dok == "✓"
            owin += ook == "✓"
        print(f"{i:38} {dok} {dm[i].get('wall_seconds',0):>5.0f}s {dm[i].get('total_tokens',0)/1000:>5.0f}k  "
              f"{ook} {om[i].get('wall_seconds',0):>5.0f}s {om[i].get('total_tokens',0)/1000:>5.0f}k")
    print(f"\nunique solves — dream: {dwin} · opencode: {owin}")

    # Speed/efficiency head-to-head on tasks both solved.
    both = [i for i in sorted(graded) if i in dres and i in ores]
    if both:
        dfast = sum(1 for i in both if dm[i]["wall_seconds"] < om[i]["wall_seconds"])
        dlean = sum(1 for i in both if dm[i]["total_tokens"] < om[i]["total_tokens"])
        print(f"\nOn the {len(both)} tasks BOTH solved:")
        print(f"  dream faster on {dfast}/{len(both)} · opencode faster on {len(both)-dfast}/{len(both)}")
        print(f"  dream fewer tokens on {dlean}/{len(both)} · opencode fewer on {len(both)-dlean}/{len(both)}")
        print(f"  median time   — dream {st.median([dm[i]['wall_seconds'] for i in both]):,.0f}s"
              f" · opencode {st.median([om[i]['wall_seconds'] for i in both]):,.0f}s")
        print(f"  median tokens — dream {st.median([dm[i]['total_tokens'] for i in both]):,.0f}"
              f" · opencode {st.median([om[i]['total_tokens'] for i in both]):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
