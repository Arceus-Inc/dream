"""Sample: end-to-end Harness.run_task with live stdio walkthrough.

What this does
--------------
Runs the full Spec-10 loop against a real LLM:

    planner  ──▶  sprint(s)  ──▶  contract  ──▶  generator  ──▶  evaluator
                                                   (tools)      (verdict)

Every macro and streaming event is printed to stdout via ``StdioObserver`` —
you will see the planner's text, every tool call the generator makes, the
evaluator's verdict, and per-sprint outcomes — no log file scraping needed.

Configuration
-------------
Auto-loads ``dream/.env.local`` (same file ``python -m dream.repl`` reads),
so if your REPL already works, this works with zero extra setup. Required
keys in that file:

    DREAM_SMOKE_API_KEY   = <your key>
    DREAM_SMOKE_MODEL     = <model / Azure deployment name>
    DREAM_SMOKE_BASE_URL  = <OpenAI-compatible base URL incl. /v1>

Run it
------
    cd q:/projects/inspired-arc/dream
    ./.venv/Scripts/python.exe ../examples/run_task_demo.py
        # or with a custom intent:
    ./.venv/Scripts/python.exe ../examples/run_task_demo.py "build a tiny CLI that prints today's date"
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path

# Force UTF-8 stdout on Windows so the observer can print box-drawing /
# arrow characters without UnicodeEncodeError under the default cp1252.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

from dream.repl._session import build_default_harness  # noqa: E402
from dream.runner import StdioObserver  # noqa: E402


_DEFAULT_INTENT = (
    "Create a small Python module `hello.py` that exposes a `greet(name)` "
    "function returning 'Hello, {name}!', plus a `test_hello.py` that "
    "imports and asserts the behaviour. Run pytest to confirm it passes."
)

# Mirrors `dream.repl.__main__._load_env_file` so a bare `python run_task_demo.py`
# picks up the same credentials the REPL uses without forcing the user to
# pre-export anything.
def _load_env_file(path: Path) -> int:
    if not path.is_file():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


def _find_env_local() -> Path | None:
    """Search common locations for the dev .env.local."""
    candidates = [
        Path.cwd() / ".env.local",                                      # if run from dream/
        Path(__file__).resolve().parent.parent / "dream" / ".env.local",  # examples/ sibling
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


async def main() -> None:
    intent = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_INTENT

    env_file = _find_env_local()
    if env_file is not None:
        n = _load_env_file(env_file)
        print(f"[demo] loaded {n} vars from {env_file}", flush=True)
    else:
        print("[demo] no .env.local found; relying on already-exported env vars", flush=True)

    missing = [k for k in ("DREAM_SMOKE_API_KEY", "DREAM_SMOKE_MODEL", "DREAM_SMOKE_BASE_URL")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"[demo] missing required env vars: {', '.join(missing)}")

    env = {
        "DREAM_SMOKE_API_KEY": os.environ["DREAM_SMOKE_API_KEY"],
        "DREAM_SMOKE_MODEL": os.environ["DREAM_SMOKE_MODEL"],
        "DREAM_SMOKE_BASE_URL": os.environ["DREAM_SMOKE_BASE_URL"],
    }

    # Worktree under a tmp dir so repeated runs don't litter the repo.
    worktree = Path(tempfile.mkdtemp(prefix="dream-demo-"))
    print(f"[demo] worktree: {worktree}", flush=True)
    print(f"[demo] model:    {env['DREAM_SMOKE_MODEL']} @ {env['DREAM_SMOKE_BASE_URL']}", flush=True)
    print(f"[demo] intent:   {intent}", flush=True)
    print("[demo] --- starting run_task; live walkthrough follows ---", flush=True)

    harness = build_default_harness(env=env, working_dir=worktree)

    async with harness:
        result = await harness.run_task(
            intent=intent,
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )

    print("[demo] --- done ---", flush=True)
    print(f"[demo] task_id:  {result.task_id}", flush=True)
    print(f"[demo] sprints:  {len(result.sprints)}", flush=True)
    for i, s in enumerate(result.sprints, 1):
        print(f"[demo]   sprint {i}: step={s.step_id} outcome={s.outcome}", flush=True)
    print(f"[demo] artifacts under: {worktree}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
