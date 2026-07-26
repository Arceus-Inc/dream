#!/usr/bin/env python3
"""Grade a harness's predictions with the OFFICIAL SWE-bench Docker harness (WSL).

Wraps ``swebench.harness.run_evaluation`` on a predictions.jsonl (one
``{instance_id, model_name_or_path, model_patch}`` per line). The official harness
builds/starts each task's container, applies model_patch + the pristine test_patch,
runs FAIL_TO_PASS + PASS_TO_PASS, and writes a canonical resolved report.

    ~/.sweb/venv/bin/python grade.py --preds results/dream/predictions.jsonl --run-id dream1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True, help="predictions.jsonl path")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite")
    args = ap.parse_args()

    preds = Path(args.preds).resolve()
    if not preds.exists():
        raise SystemExit(f"predictions not found: {preds}")

    ids = [json.loads(l)["instance_id"] for l in preds.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"[grade] {len(ids)} prediction(s) · run_id={args.run_id}", flush=True)

    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", args.dataset,
        "--split", "test",
        "--predictions_path", str(preds),
        "--run_id", args.run_id,
        "--max_workers", str(args.max_workers),
        "--namespace", "swebench",
        "--cache_level", "env",
    ]
    print("[grade] " + " ".join(cmd), flush=True)
    # Run from the results dir so the report json lands next to the predictions.
    proc = subprocess.run(cmd, cwd=str(preds.parent))
    rc = proc.returncode

    # The harness writes <model>.<run_id>.json in cwd; surface the resolved summary.
    reports = sorted(preds.parent.glob(f"*.{args.run_id}.json"))
    if reports:
        rep = json.loads(reports[-1].read_text(encoding="utf-8"))
        resolved = rep.get("resolved_instances", rep.get("resolved", "?"))
        total = rep.get("total_instances", len(ids))
        print(f"\n[grade] RESOLVED {resolved}/{total}  (report: {reports[-1].name})", flush=True)
        print(f"[grade] report keys: {list(rep)[:12]}", flush=True)
    else:
        print("[grade] no report json found (check harness output above)", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
