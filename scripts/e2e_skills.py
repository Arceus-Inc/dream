"""E2E: skills auto-wire through ``harness.run_task`` against the live model.

Proves the Phase-1 wiring end to end, not in a unit mock. A workspace skill
that mandates a unique, checkable rule must be *discovered, loaded, and
applied* by the generator during a real planner→sprint→evaluator run. We
verify by inspecting the produced artifact — the model's actual output is the
oracle, not its words.

Scenarios (3 cheap catalogue checks + 3 live ``run_task`` runs):
  1. one skill                → name appears in the assembled system prompt
  2. skills=False             → name ABSENT from the prompt
  3. two skills               → both names in the prompt
  4. hard-rule header skill   → run_task → produced greet.py starts with header
  5. control (skills=False)   → run_task → produced greet.py has NO header
  6. naming-convention skill  → run_task → produced function is named `salute`

Credentials: ~/Arceus/.env.local (Azure), mapped to DREAM_*.
Run:  uv run python scripts/e2e_skills.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from dream import SessionOptions, build_harness
from dream.runner import StdioObserver

HEADER = "# ARCEUS-HEADER-7F3A"

_HEADER_SKILL = f"""\
---
name: arceus-file-header
description: The mandatory header every new Python source file must carry.
when_to_use: Whenever you create or write any new Python source file.
---
# Arceus file-header convention

Every new Python file you create MUST begin with this EXACT line as its very
first line:

    {HEADER}

This is a hard, non-negotiable project rule. Apply it to every .py file.
"""

_COMMIT_SKILL = """\
---
name: arceus-commit-style
description: How commit messages must be phrased in this project.
when_to_use: Whenever you write a git commit message.
---
# Commit style
Use imperative mood, lowercase, no trailing period.
"""

_NAMING_SKILL = """\
---
name: arceus-greeting-naming
description: The required name for any greeting function in this project.
when_to_use: Whenever you write a function that greets or salutes a person.
---
# Greeting-function naming rule

In this project, a function that greets a person MUST be named exactly
`salute` (never `greet`, `hello`, or `greeting`). This is a hard rule.
"""

_INTENT = (
    "Create a Python module `greet.py` exposing `greet(name)` that returns "
    "the string 'Hi ' followed by the name. Also create `test_greet.py` that "
    "imports greet and asserts greet('Sam') == 'Hi Sam'. Run pytest to confirm."
)

_NAMING_INTENT = (
    "Create a Python module `greet.py` with a function that greets a person "
    "by returning 'Hi ' followed by their name, plus a test that asserts the "
    "behaviour for the name 'Sam'. Run pytest to confirm it passes."
)


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


def _fresh_workspace(home: Path, *skills: tuple[str, str]) -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-skills-", dir=home))
    _git_init(wt)
    for slug, body in skills:
        d = wt / "docs" / "skills" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
    return wt


def _greet_text(wt: Path) -> str:
    greet = wt / "greet.py"
    return greet.read_text(encoding="utf-8") if greet.exists() else ""


async def _run_task(creds: dict[str, str], wt: Path, intent: str, *, skills: bool) -> int:
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, skills=skills,
    )
    async with harness:
        result = await harness.run_task(
            intent=intent, observer=StdioObserver(sys.stdout), max_sprints=4
        )
    return len(result.sprints)


def _system_prompt(creds: dict[str, str], wt: Path, *, skills: bool = True) -> str:
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt, skills=skills,
    )
    engine = harness.config._engine_factory("probe", SessionOptions())  # type: ignore[misc]
    return engine.streamer._system_prompt or ""  # type: ignore[attr-defined]


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-skills-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    # --- cheap catalogue checks (no LLM) ---------------------------------
    wt1 = _fresh_workspace(home, ("arceus-file-header", _HEADER_SKILL))
    p1 = _system_prompt(creds, wt1)
    results.append(("1 one-skill in prompt", "arceus-file-header" in p1, ""))

    p2 = _system_prompt(creds, wt1, skills=False)
    results.append(("2 skills=False omits it", "arceus-file-header" not in p2, ""))

    wt3 = _fresh_workspace(
        home, ("arceus-file-header", _HEADER_SKILL), ("arceus-commit-style", _COMMIT_SKILL)
    )
    p3 = _system_prompt(creds, wt3)
    results.append(
        ("3 two skills in prompt",
         "arceus-file-header" in p3 and "arceus-commit-style" in p3, "")
    )

    # --- live run_task scenarios -----------------------------------------
    print("\n[scenario 4] LIVE: hard-rule header skill → run_task\n" + "-" * 60)
    wt4 = _fresh_workspace(home, ("arceus-file-header", _HEADER_SKILL))
    await _run_task(creds, wt4, _INTENT, skills=True)
    g4 = _greet_text(wt4)
    results.append(("4 header applied", g4.lstrip().startswith(HEADER), wt4.name))

    print("\n[scenario 5] LIVE: control (skills=False) → run_task\n" + "-" * 60)
    wt5 = _fresh_workspace(home, ("arceus-file-header", _HEADER_SKILL))
    await _run_task(creds, wt5, _INTENT, skills=False)
    g5 = _greet_text(wt5)
    results.append(("5 control: header absent", not g5.lstrip().startswith(HEADER), wt5.name))

    print("\n[scenario 6] LIVE: naming-convention skill → run_task\n" + "-" * 60)
    wt6 = _fresh_workspace(home, ("arceus-greeting-naming", _NAMING_SKILL))
    await _run_task(creds, wt6, _NAMING_INTENT, skills=True)
    g6 = _greet_text(wt6)
    results.append(("6 naming rule applied", "def salute" in g6, wt6.name))

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
    print("[e2e] all 6 scenarios PASS — skills auto-wire verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
