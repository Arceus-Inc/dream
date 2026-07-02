#!/usr/bin/env python3
"""dream-eval runner for swe-bench-verified-100 (Model C — host venv, no Docker).

Phase 1 capability: `--validate-gold <instance_id>` proves the env + grading
harness end to end on a single task:
  clone repo@base_commit → uv venv (spec python) → pip install -e . →
  apply test_patch → FAIL_TO_PASS should FAIL (red) →
  apply gold_patch → FAIL_TO_PASS should PASS + PASS_TO_PASS hold (resolved).

Phase 3 (`--dream`) wraps Harness.run_task in the same env (added next).

Env/install specs are reused from the `swebench` package
(`MAP_REPO_VERSION_TO_SPECS`); run this script under uv so it's importable:
    uv run --with swebench --python .venv/bin/python datasets/swe-bench-verified-100/eval.py --validate-gold psf__requests-2931
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = HERE / "tasks.jsonl"
CACHE = Path.home() / ".cache" / "dream-eval"
REPOS = CACHE / "repos"


def load_task(instance_id: str) -> dict:
    for line in TASKS.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["instance_id"] == instance_id:
            return r
    raise SystemExit(f"instance not found: {instance_id}")


def spec_for(repo: str, version: str) -> dict:
    from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS

    table = MAP_REPO_VERSION_TO_SPECS[repo]
    if version not in table:  # pick the nearest known version string
        version = sorted(table, key=lambda v: abs(len(v) - len(version)))[0]
    return table[version]


def _run(cmd: list[str], cwd: Path, env: dict | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    import os

    full = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=cwd, env=full, capture_output=True, text=True, timeout=timeout)


def clone_at(repo: str, base_commit: str, dest: Path) -> None:
    """Clone (cached bare mirror) and checkout base_commit into dest."""
    REPOS.mkdir(parents=True, exist_ok=True)
    mirror = REPOS / (repo.replace("/", "__") + ".git")
    if not mirror.exists():
        print(f"[env] mirroring {repo} …", flush=True)
        subprocess.run(["git", "clone", "--bare", f"https://github.com/{repo}.git", str(mirror)], check=True)
    subprocess.run(["git", "clone", "-q", str(mirror), str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", base_commit], check=True)


def make_env(worktree: Path, spec: dict) -> Path:
    """uv venv with the spec python + editable install; return the venv python."""
    py = spec.get("python", "3.11")
    venv = worktree / ".eval-venv"
    print(f"[env] uv venv python={py} …", flush=True)
    subprocess.run(["uv", "venv", "-q", "--python", py, str(venv)], check=True, cwd=worktree)
    vpy = venv / "bin" / "python"
    # Editable install so worktree source edits are live; + test packages.
    pkgs = spec.get("packages", "")
    pip_pkgs = spec.get("pip_packages", [])
    print("[env] installing project (editable) + test deps …", flush=True)
    subprocess.run(["uv", "pip", "install", "-q", "--python", str(vpy), "-e", "."], check=True, cwd=worktree)
    extra = ([p for p in pkgs.split() if p] if isinstance(pkgs, str) else list(pkgs)) + list(pip_pkgs)
    extra = [p for p in extra if p and p not in {"requirements.txt"}]
    if extra:
        subprocess.run(["uv", "pip", "install", "-q", "--python", str(vpy), *extra], cwd=worktree)
    return vpy


def git_apply(worktree: Path, patch: str, label: str) -> bool:
    pf = worktree / f".{label}.diff"
    pf.write_text(patch, encoding="utf-8")
    for args in (["git", "apply", "-v", str(pf)], ["git", "apply", "-v", "--3way", str(pf)], ["patch", "-p1", "-i", str(pf)]):
        p = _run(args, worktree)
        if p.returncode == 0:
            return True
    print(f"[apply:{label}] FAILED\n{p.stdout}\n{p.stderr}", flush=True)
    return False


_STATUS = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(\S+)", re.M)


def run_tests(vpy: Path, worktree: Path, node_ids: list[str]) -> dict[str, str]:
    """Run pytest on exact node ids; return {nodeid: PASSED|FAILED|ERROR|MISSING}."""
    if not node_ids:
        return {}
    cmd = [str(vpy), "-m", "pytest", "-rA", "--tb=no", "-p", "no:cacheprovider", "-q", *node_ids]
    p = _run(cmd, worktree, timeout=1200)
    out = p.stdout + p.stderr
    found = {nid: st for st, nid in _STATUS.findall(out)}
    return {nid: found.get(nid, "MISSING") for nid in node_ids}


def classify(f2p: dict[str, str], p2p: dict[str, str]) -> str:
    f2p_ok = all(v == "PASSED" for v in f2p.values()) and bool(f2p)
    p2p_ok = all(v == "PASSED" for v in p2p.values())
    if f2p_ok and p2p_ok:
        return "RESOLVED"
    if not p2p_ok:
        return "REGRESSION"
    return "FAIL"


def validate_gold(instance_id: str) -> int:
    t = load_task(instance_id)
    spec = spec_for(t["repo"], t["version"])
    print(f"=== {instance_id}  ({t['repo']} {t['version']}, {t['difficulty']}) ===")
    print(f"FAIL_TO_PASS: {len(t['FAIL_TO_PASS'])} | PASS_TO_PASS: {len(t['PASS_TO_PASS'])}")
    with tempfile.TemporaryDirectory(prefix="deval-") as td:
        wt = Path(td) / "repo"
        clone_at(t["repo"], t["base_commit"], wt)
        vpy = make_env(wt, spec)
        if not git_apply(wt, t["test_patch"], "test"):
            return 2
        # 1) Before the fix: FAIL_TO_PASS must be red.
        before = run_tests(vpy, wt, t["FAIL_TO_PASS"])
        print(f"[red ] FAIL_TO_PASS before gold: {before}")
        # 2) Apply the gold fix: FAIL_TO_PASS should flip green, PASS_TO_PASS hold.
        if not git_apply(wt, t["gold_patch"], "gold"):
            return 2
        f2p = run_tests(vpy, wt, t["FAIL_TO_PASS"])
        p2p = run_tests(vpy, wt, t["PASS_TO_PASS"][:60])  # cap for speed in validation
        verdict = classify(f2p, p2p)
        print(f"[green] FAIL_TO_PASS after gold: {f2p}")
        print(f"[green] PASS_TO_PASS (first {len(p2p)}): {sum(v=='PASSED' for v in p2p.values())}/{len(p2p)} passed")
        red_ok = all(v != "PASSED" for v in before.values())
        print(f"\nHARNESS CHECK: red-before={'ok' if red_ok else 'NO'}  ·  gold-verdict={verdict}")
        return 0 if (red_ok and verdict == "RESOLVED") else 1


def _test_files(test_patch: str) -> list[str]:
    """The b/ paths touched by the test patch (the oracle test files)."""
    return re.findall(r"^\+\+\+ b/(\S+)", test_patch, re.M)


def _intent(t: dict, test_cmd: str) -> str:
    return (
        f"You are fixing a real bug in the `{t['repo']}` repository, already checked "
        f"out in your working directory at the buggy commit.\n\n"
        f"ISSUE\n-----\n{t['problem_statement']}\n\n"
        f"TASK\n----\n"
        f"Edit the library SOURCE files to resolve the issue. Do NOT edit the test "
        f"files. The acceptance check is this exact command (run it to verify):\n\n"
        f"    {test_cmd}\n\n"
        f"It must exit 0 (all listed tests pass). Keep the change minimal and do not "
        f"break unrelated tests."
    )


async def _run_dream(t: dict, spec: dict, max_sprints: int) -> int:
    import os

    from dream.repl._session import build_default_harness
    from dream.runner import StdioObserver

    miss = [k for k in ("DREAM_SMOKE_API_KEY", "DREAM_SMOKE_MODEL", "DREAM_SMOKE_BASE_URL") if not os.environ.get(k)]
    if miss:
        raise SystemExit("missing env for live run: " + ", ".join(miss))
    env = {k: os.environ[k] for k in ("DREAM_SMOKE_API_KEY", "DREAM_SMOKE_MODEL", "DREAM_SMOKE_BASE_URL")}

    with tempfile.TemporaryDirectory(prefix="deval-dream-") as td:
        wt = Path(td) / "repo"
        clone_at(t["repo"], t["base_commit"], wt)
        vpy = make_env(wt, spec)
        # Apply the oracle tests and commit them so (a) FAIL_TO_PASS exists for the
        # in-loop check and (b) we have a baseline to diff dream's patch against and
        # to restore canonical tests for grading.
        if not git_apply(wt, t["test_patch"], "test"):
            return 2
        _run(["git", "add", "-A"], wt)
        _run(["git", "-c", "user.email=e@x.io", "-c", "user.name=deval", "commit", "-q", "-m", "tests"], wt)
        tests_commit = _run(["git", "rev-parse", "HEAD"], wt).stdout.strip()
        (wt / ".harness").mkdir(parents=True, exist_ok=True)
        (wt / ".harness" / "sandbox.toml").write_text('tier = "unrestricted"\nconfirm_unrestricted = true\n', encoding="utf-8")

        f2p = t["FAIL_TO_PASS"]
        test_cmd = f"{shlex.quote(str(vpy))} -m pytest -p no:cacheprovider -q " + " ".join(shlex.quote(x) for x in f2p)
        print(f"\n=== DREAM run: {t['instance_id']} ({t['repo']} {t['version']}, {t['difficulty']}) ===", flush=True)
        print(f"[oracle] {test_cmd}\n", flush=True)

        harness = build_default_harness(env=env, working_dir=wt)
        async with harness:
            result = await harness.run_task(
                intent=_intent(t, test_cmd),
                worktree_root=wt,
                verification_steps=({"kind": "pytest", "command": test_cmd},),
                observer=StdioObserver(sys.stdout),
                max_sprints=max_sprints,
            )

        # Neutralize any test tampering: restore the canonical oracle test files.
        for f in _test_files(t["test_patch"]):
            _run(["git", "checkout", tests_commit, "--", f], wt)
        f2p_res = run_tests(vpy, wt, t["FAIL_TO_PASS"])
        p2p_res = run_tests(vpy, wt, t["PASS_TO_PASS"][:60])
        verdict = classify(f2p_res, p2p_res)
        model_patch = _run(["git", "diff", tests_commit], wt).stdout

        rec = {
            "instance_id": t["instance_id"],
            "repo": t["repo"],
            "difficulty": t["difficulty"],
            "verdict": verdict,
            "fail_to_pass": f2p_res,
            "pass_to_pass_sample": f"{sum(v == 'PASSED' for v in p2p_res.values())}/{len(p2p_res)}",
            "sprints": [{"step": s.step_id, "outcome": s.outcome} for s in result.sprints],
            "model_patch_lines": model_patch.count("\n"),
        }
        outdir = HERE / "results"
        outdir.mkdir(exist_ok=True)
        (outdir / f"{t['instance_id']}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        (outdir / f"{t['instance_id']}.patch").write_text(model_patch, encoding="utf-8")
        print(f"\n=== VERDICT {t['instance_id']}: {verdict} ===")
        print(f"FAIL_TO_PASS: {f2p_res}")
        print(f"PASS_TO_PASS (first {len(p2p_res)}): {rec['pass_to_pass_sample']}")
        print(f"sprints: {rec['sprints']}")
        print(f"patch: {rec['model_patch_lines']} lines -> results/{t['instance_id']}.patch")
        return 0


def run_dream(instance_id: str, max_sprints: int) -> int:
    import asyncio

    t = load_task(instance_id)
    spec = spec_for(t["repo"], t["version"])
    return asyncio.run(_run_dream(t, spec, max_sprints))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate-gold", metavar="INSTANCE_ID")
    ap.add_argument("--dream", metavar="INSTANCE_ID")
    ap.add_argument("--max-sprints", type=int, default=3)
    args = ap.parse_args()
    if args.validate_gold:
        return validate_gold(args.validate_gold)
    if args.dream:
        return run_dream(args.dream, args.max_sprints)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
