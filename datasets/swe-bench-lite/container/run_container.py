#!/usr/bin/env python3
"""Orchestrate in-container agent runs on SWE-bench Lite (runs in WSL/Linux).

For each task: resolve+pull the SWE-bench eval image, start a container, bootstrap
the agent, run it against /testbed (with the real test env as its oracle), then
extract the source diff as ``model_patch``. Predictions + metrics are written per
harness; grading is done separately by ``grade.py`` (official swebench harness).

    export BENCH_MODEL_API_KEY=…
    ~/.sweb/venv/bin/python run_container.py --harness dream --only pallets__flask-4045
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # datasets/swe-bench-lite
TASKS = ROOT / "tasks.jsonl"
RESULTS = ROOT / "results"
DREAM_SRC = ROOT.parent.parent  # the dream repo root

BASE_URL = os.environ.get("BENCH_MODEL_BASE_URL", "")
MODEL = os.environ.get("BENCH_MODEL", "")

# Paths that must never appear in the model_patch.
_EXCLUDE_PATHSPECS = [
    ":(exclude).harness",
    ":(exclude,glob)**/.harness/**",
    ":(exclude,glob)docs/exec-plans/**",
    ":(exclude,glob)docs/evals/**",
    ":(exclude,glob)**/.dream/**",
    ":(exclude,glob)**/tests/**",
    ":(exclude,glob)**/test_*.py",
    ":(exclude,glob)**/*_test.py",
    ":(exclude,glob)**/conftest.py",
]


def load_tasks() -> list[dict]:
    return [json.loads(l) for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()]


def sh(cmd: list[str], timeout: int | None = None, check: bool = False) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-800:]}")
    return p


def image_key(task: dict) -> str:
    from swebench.harness.test_spec.test_spec import make_test_spec

    return make_test_spec(task, namespace="swebench").instance_image_key


def ensure_image(img: str) -> None:
    if sh(["docker", "image", "inspect", img]).returncode == 0:
        return
    print(f"[img] pulling {img} …", flush=True)
    p = sh(["docker", "pull", img], timeout=1200)
    if p.returncode != 0:
        raise RuntimeError(f"pull failed for {img}: {p.stderr[-400:]}")


def dexec(container: str, script: str, timeout: int | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [container, "bash", "-lc", script]
    return sh(cmd, timeout=timeout)


def extract_patch(container: str, base_commit: str) -> str:
    dexec(container, "cd /testbed && git add -A", timeout=120)
    q = " ".join(shlex.quote(p) for p in _EXCLUDE_PATHSPECS)
    p = dexec(container, f"cd /testbed && git diff --cached --no-color {base_commit} -- . {q}", timeout=120)
    return p.stdout


def apply_test_patch(container: str, task: dict) -> str:
    """Apply the oracle test_patch into /testbed so FAIL_TO_PASS tests EXIST for the agent to
    verify against (oracle-assisted mode). These test files are excluded from the model_patch,
    and canonical grading re-applies the pristine test_patch, so this can't inflate the score.
    Returns the commit sha AFTER applying tests (the base the agent's diff is taken against)."""
    tp = task.get("test_patch") or ""
    if not tp.strip():
        return task["base_commit"]
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as tf:
        tf.write(tp)
        host_patch = tf.name
    try:
        sh(["docker", "cp", host_patch, f"{container}:/out/test.patch"], check=True)
    finally:
        os.unlink(host_patch)
    # Try a sequence of appliers; commit so it's part of the base the agent sees.
    script = (
        "cd /testbed && "
        "(git apply -v /out/test.patch || git apply -v --3way /out/test.patch || patch -p1 -i /out/test.patch) && "
        "git add -A && git -c user.email=e@x.io -c user.name=bench commit -q -m 'oracle tests' && echo APPLIED"
    )
    r = dexec(container, script, timeout=180)
    if "APPLIED" not in r.stdout:
        raise RuntimeError(f"test_patch apply failed: {(r.stdout + r.stderr)[-500:]}")
    sha = dexec(container, "cd /testbed && git rev-parse HEAD").stdout.strip()
    return sha


def bootstrap_dream(container: str, wheel_in_container: str) -> None:
    # uv + an isolated py3.11 venv + the CURRENT dream wheel (deps from PyPI).
    r = dexec(container, "command -v uv >/dev/null 2>&1 || (curl -LsSf https://astral.sh/uv/install.sh | sh)", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"uv install failed: {r.stderr[-500:]}")
    r = dexec(container, "~/.local/bin/uv venv /opt/dv --python 3.11", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"venv create failed: {r.stderr[-500:]}")
    r = dexec(container, f"~/.local/bin/uv pip install --python /opt/dv/bin/python {shlex.quote(wheel_in_container)}", timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"dream install failed: {r.stderr[-800:]}")


def run_dream(task: dict, args, wheel_host: Path) -> tuple[str, dict]:
    iid = task["instance_id"]
    img = image_key(task)
    ensure_image(img)
    key = os.environ.get("BENCH_MODEL_API_KEY") or os.environ.get("PODIUM_MODEL_API_KEY", "")
    container = f"swebench-dream-{uuid.uuid4().hex[:8]}"
    metrics: dict = {"instance_id": iid, "harness": "dream", "model": MODEL}
    patch = ""
    t0 = time.perf_counter()
    try:
        sh([
            "docker", "run", "-d", "--name", container,
            "-e", f"DREAM_SMOKE_API_KEY={key}",
            "-e", f"DREAM_SMOKE_MODEL={MODEL}",
            "-e", f"DREAM_SMOKE_BASE_URL={BASE_URL}",
            img, "sleep", "infinity",
        ], check=True, timeout=120)
        dexec(container, "mkdir -p /out")
        # Ship the wheel (keep its versioned name — uv rejects a renamed wheel), entry, and task.
        wheel_ct = f"/tmp/{wheel_host.name}"
        sh(["docker", "cp", str(wheel_host), f"{container}:{wheel_ct}"], check=True)
        sh(["docker", "cp", str(HERE / "dream_entry.py"), f"{container}:/opt/dream_entry.py"], check=True)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(task, tf)
            task_json = tf.name
        sh(["docker", "cp", task_json, f"{container}:/out/task.json"], check=True)
        os.unlink(task_json)

        agent_base = apply_test_patch(container, task)
        bootstrap_dream(container, wheel_ct)

        runenv = {
            "DREAM_SMOKE_API_KEY": key, "DREAM_SMOKE_MODEL": MODEL, "DREAM_SMOKE_BASE_URL": BASE_URL,
            "BENCH_TASK_JSON": "/out/task.json",
            "BENCH_TESTBED_PY": "/opt/miniconda3/envs/testbed/bin/python",
            "BENCH_MAX_SPRINTS": str(args.max_sprints), "BENCH_MAX_TURNS": str(args.max_turns),
            "PYTHONUNBUFFERED": "1",
        }
        r = dexec(container, "/opt/dv/bin/python /opt/dream_entry.py", timeout=args.timeout, env=runenv)
        print(r.stdout[-2000:], flush=True)
        if r.returncode != 0:
            metrics.update(ok=False, error=f"entry_rc={r.returncode}: {r.stderr[-400:]}")
        res = dexec(container, "cat /out/result.json 2>/dev/null || echo '{}'")
        try:
            metrics.update(json.loads(res.stdout))
        except json.JSONDecodeError:
            metrics.setdefault("error", "no result.json")
        patch = extract_patch(container, agent_base)
    except Exception as exc:  # noqa: BLE001
        metrics.setdefault("error", f"{type(exc).__name__}: {exc}"[:500])
        metrics.setdefault("ok", False)
    finally:
        if not args.keep:
            sh(["docker", "rm", "-f", container])
    metrics["wall_seconds"] = round(time.perf_counter() - t0, 1)
    metrics["patch_len"] = len(patch)
    metrics["empty_patch"] = not patch.strip()
    return patch, metrics


def build_wheel() -> Path:
    out = Path(tempfile.mkdtemp(prefix="dreamwheel-"))
    print("[build] building dream wheel …", flush=True)
    p = sh(["bash", "-lc", f"cd {shlex.quote(str(DREAM_SRC))} && ~/.local/bin/uv build --wheel -o {shlex.quote(str(out))}"], timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"wheel build failed: {p.stderr[-800:]}")
    wheels = sorted(out.glob("*.whl"))
    if not wheels:
        raise RuntimeError("no wheel produced")
    print(f"[build] {wheels[-1].name}", flush=True)
    return wheels[-1]


def build_prompt(task: dict, testbed_py: str) -> str:
    f2p = task["FAIL_TO_PASS"]
    test_cmd = f"{testbed_py} -m pytest -p no:cacheprovider -q " + " ".join(f2p)
    return (
        f"You are fixing a real bug in the `{task['repo']}` repository, already checked out at "
        f"/testbed at the buggy commit.\n\n"
        f"ISSUE\n-----\n{task['problem_statement']}\n\n"
        f"TASK\n----\n"
        f"Edit the library SOURCE files under /testbed to resolve the issue. Do NOT edit or add test "
        f"files. The acceptance check is this exact command (run it to verify):\n\n    {test_cmd}\n\n"
        f"It must exit 0 (all listed tests pass). Keep the change minimal and do not break unrelated "
        f"behavior. When the acceptance command passes, you are done."
    )


def bootstrap_opencode(container: str) -> None:
    r = dexec(container, "command -v opencode >/dev/null 2>&1 || (curl -fsSL https://opencode.ai/install | bash)", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"opencode install failed: {(r.stdout + r.stderr)[-600:]}")
    # Write the custom Azure (OpenAI-compatible) provider config.
    tmpl = (HERE / "opencode_config.template.json").read_text(encoding="utf-8")
    cfg = tmpl.replace("__BASE_URL__", BASE_URL).replace("__MODEL__", MODEL)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        tf.write(cfg)
        cfg_host = tf.name
    dexec(container, "mkdir -p /root/.config/opencode")
    sh(["docker", "cp", cfg_host, f"{container}:/root/.config/opencode/opencode.json"], check=True)
    os.unlink(cfg_host)
    ver = dexec(container, "export PATH=$HOME/.opencode/bin:$HOME/.local/bin:$PATH; opencode --version")
    print(f"[opencode] version {ver.stdout.strip() or ver.stderr.strip()}", flush=True)


def parse_opencode_usage(text: str) -> dict:
    """Token/cost extraction from `opencode run --format json` output (ndjson events).

    Each assistant turn emits a ``step_finish`` event carrying ``tokens``
    ``{input,output,reasoning,total,cache}`` and ``cost``. We SUM across turns — the same
    per-turn billed accounting dream's UsageMeter uses — so the two are comparable.
    """
    it = ot = rt = tt = 0
    cost = 0.0
    steps = 0
    found = False

    def find_tokens(obj: object):
        if isinstance(obj, dict):
            tok = obj.get("tokens")
            c = obj.get("cost")
            if isinstance(tok, dict) and ("input" in tok or "output" in tok):
                return tok, (c if isinstance(c, (int, float)) else None)
            for v in obj.values():
                r = find_tokens(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = find_tokens(v)
                if r:
                    return r
        return None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "step_finish":
            continue
        res = find_tokens(ev)
        if not res:
            continue
        tok, c = res
        found = True
        steps += 1
        it += int(tok.get("input", 0) or 0)
        ot += int(tok.get("output", 0) or 0)
        rt += int(tok.get("reasoning", 0) or 0)
        tt += int(tok.get("total", 0) or 0)
        if c is not None:
            cost += float(c)

    total = tt or (it + ot + rt)
    return {"input_tokens": it, "output_tokens": ot, "reasoning_tokens": rt,
            "total_tokens": total, "cost": round(cost, 4), "steps": steps,
            "usage_found": found}


def run_opencode(task: dict, args) -> tuple[str, dict]:
    iid = task["instance_id"]
    img = image_key(task)
    ensure_image(img)
    key = os.environ.get("BENCH_MODEL_API_KEY") or os.environ.get("PODIUM_MODEL_API_KEY", "")
    container = f"swebench-opencode-{uuid.uuid4().hex[:8]}"
    metrics: dict = {"instance_id": iid, "harness": "opencode", "model": MODEL}
    patch = ""
    t0 = time.perf_counter()
    try:
        sh([
            "docker", "run", "-d", "--name", container,
            "-e", f"BENCH_MODEL_API_KEY={key}",
            "-e", "OPENCODE_DISABLE_AUTOUPDATE=1",
            img, "sleep", "infinity",
        ], check=True, timeout=120)
        dexec(container, "mkdir -p /out")
        agent_base = apply_test_patch(container, task)
        bootstrap_opencode(container)

        prompt = build_prompt(task, "/opt/miniconda3/envs/testbed/bin/python")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
            tf.write(prompt)
            prompt_host = tf.name
        sh(["docker", "cp", prompt_host, f"{container}:/out/prompt.txt"], check=True)
        os.unlink(prompt_host)

        run_script = (
            "export PATH=$HOME/.opencode/bin:$HOME/.local/bin:$PATH; cd /testbed; "
            f"opencode run --dir /testbed --model azure/{MODEL} --format json --auto "
            '"$(cat /out/prompt.txt)" > /out/opencode.out 2> /out/opencode.err'
        )
        r = dexec(container, run_script, timeout=args.timeout, env={"BENCH_MODEL_API_KEY": key})
        out = dexec(container, "cat /out/opencode.out 2>/dev/null | tail -c 200000").stdout
        err = dexec(container, "tail -c 1500 /out/opencode.err 2>/dev/null").stdout
        usage = parse_opencode_usage(out)
        metrics.update(usage)
        metrics["ok"] = r.returncode == 0 and usage.get("usage_found", False)
        if r.returncode != 0:
            metrics["error"] = f"run_rc={r.returncode}: {err[-400:]}"
        elif not usage.get("usage_found"):
            metrics["error"] = f"no usage parsed; stderr={err[-300:]}"
        patch = extract_patch(container, agent_base)
    except Exception as exc:  # noqa: BLE001
        metrics.setdefault("error", f"{type(exc).__name__}: {exc}"[:500])
        metrics.setdefault("ok", False)
    finally:
        if not args.keep:
            sh(["docker", "rm", "-f", container])
    metrics["wall_seconds"] = round(time.perf_counter() - t0, 1)
    metrics["patch_len"] = len(patch)
    metrics["empty_patch"] = not patch.strip()
    return patch, metrics


def done_instances(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.add(json.loads(line)["instance_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return out


def write_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=["dream", "opencode"], default="dream")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-sprints", type=int, default=6)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--keep", action="store_true", help="don't remove the container (debug)")
    args = ap.parse_args()

    if not (os.environ.get("BENCH_MODEL_API_KEY") or os.environ.get("PODIUM_MODEL_API_KEY")):
        raise SystemExit("set BENCH_MODEL_API_KEY (or PODIUM_MODEL_API_KEY)")
    if not BASE_URL or not MODEL:
        raise SystemExit(
            "set BENCH_MODEL_BASE_URL (an OpenAI-compatible /v1 endpoint) and BENCH_MODEL"
        )

    tasks = load_tasks()
    if args.only:
        tasks = [t for t in tasks if t["instance_id"] == args.only]
    preds = RESULTS / args.harness / "predictions.jsonl"
    mets = RESULTS / args.harness / "metrics.jsonl"
    done = done_instances(mets)
    tasks = [t for t in tasks if t["instance_id"] not in done]
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"[{args.harness}] {len(tasks)} task(s); {len(done)} already done; model={MODEL}", flush=True)

    wheel = build_wheel() if args.harness == "dream" else None
    for i, task in enumerate(tasks, 1):
        iid = task["instance_id"]
        print(f"\n[{args.harness} {i}/{len(tasks)}] {iid} ({task['repo']}) …", flush=True)
        if args.harness == "dream":
            patch, metrics = run_dream(task, args, wheel)
        else:
            patch, metrics = run_opencode(task, args)
        write_jsonl(preds, {"instance_id": iid, "model_name_or_path": args.harness, "model_patch": patch})
        write_jsonl(mets, metrics)
        print(f"[{args.harness} {i}/{len(tasks)}] {iid} ok={metrics.get('ok')} "
              f"{metrics.get('wall_seconds')}s tokens={metrics.get('total_tokens')} patch={metrics.get('patch_len')}B", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
