"""Configurable run_task driver — every component from the command line.

Point it at an intent and a workspace, toggle or feed each component, and it
builds the harness and runs the full planner → sprint → evaluator loop:

  uv run python consumer-facing-api/run_harness.py \\
      --intent "Create hello.py that prints hello" \\
      --workspace /tmp/demo \\
      --skill ./my-rule/SKILL.md \\
      --memory-file ./facts/naming.md \\
      --plugin ./plugins/ticket-stamper \\
      --mcp-allowlist ./mcp-allowlist.toml \\
      --sandbox-tier repo-write --max-sprints 5

Credentials: --model/--api-key/--base-url flags, else DREAM_MODEL /
DREAM_API_KEY / DREAM_BASE_URL env vars (optionally loaded via --env-file).

Component inputs:
  --skill         SKILL.md file (or a dir containing one); repeatable.
                  Installed into <workspace>/docs/skills/<name>/.
  --memory-file   markdown memory record; repeatable. Seeded into the
                  project memory dir under DREAM_HOME.
  --plugin        plugin directory (manifest.toml + entry); repeatable.
                  Copied into <workspace>/plugins/ and enabled.
  --mcp-allowlist a .toml copied to <workspace>/.harness/mcp-allowlist.toml.
  --no-skills / --no-memory / --no-mcp / --no-plugins   disable a surface.
  --working-memory   opt into the task scratchpad + memory_propose seam (off by default).
  --sandbox-tier  read-only | repo-write | repo-write+net-allowlist | unrestricted
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dream import build_harness
from dream.runner import StdioObserver

TIERS = ("read-only", "repo-write", "repo-write+net-allowlist", "unrestricted")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a dream task with every component configurable.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--intent", required=True, help="what the task should achieve")
    parser.add_argument(
        "--workspace", type=Path, default=None,
        help="git workspace (created + git-initialised if missing; default: temp dir)",
    )
    creds = parser.add_argument_group("credentials")
    creds.add_argument("--model", default=None, help="model name (else $DREAM_MODEL)")
    creds.add_argument("--api-key", default=None, help="API key (else $DREAM_API_KEY)")
    creds.add_argument("--base-url", default=None, help="endpoint (else $DREAM_BASE_URL)")
    creds.add_argument("--env-file", type=Path, default=None, help="KEY=VALUE file to load")

    comp = parser.add_argument_group("component inputs")
    comp.add_argument("--skill", type=Path, action="append", default=[],
                      help="SKILL.md file or dir to install (repeatable)")
    comp.add_argument("--memory-file", type=Path, action="append", default=[],
                      help="markdown memory record to seed (repeatable)")
    comp.add_argument("--plugin", type=Path, action="append", default=[],
                      help="plugin dir to install + enable (repeatable)")
    comp.add_argument("--mcp-allowlist", type=Path, default=None,
                      help="mcp-allowlist.toml to install")

    toggles = parser.add_argument_group("component toggles")
    toggles.add_argument("--no-skills", action="store_true")
    toggles.add_argument("--no-memory", action="store_true")
    toggles.add_argument("--no-mcp", action="store_true")
    toggles.add_argument("--no-plugins", action="store_true")
    toggles.add_argument(
        "--working-memory", action="store_true",
        help="opt into the task scratchpad + memory_propose seam (off by default)",
    )

    run = parser.add_argument_group("run knobs")
    run.add_argument("--sandbox-tier", choices=TIERS, default="repo-write")
    run.add_argument("--max-sprints", type=int, default=6)
    run.add_argument("--max-turns", type=int, default=8)
    run.add_argument("--wake-model", default=None,
                     help="cheaper model for wake heartbeats (always-on agents)")
    return parser.parse_args()


def resolve_creds(args: argparse.Namespace) -> dict[str, str]:
    if args.env_file:
        for line in args.env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    model = args.model or os.environ.get("DREAM_MODEL")
    api_key = args.api_key or os.environ.get("DREAM_API_KEY")
    base_url = args.base_url or os.environ.get("DREAM_BASE_URL")
    missing = [n for n, v in
               (("model", model), ("api-key", api_key), ("base-url", base_url)) if not v]
    if missing:
        raise SystemExit(f"missing credentials: {', '.join(missing)} "
                         "(flags or DREAM_* env vars)")
    assert model and api_key and base_url
    return {"model": model, "api_key": api_key, "base_url": base_url}


def ensure_workspace(path: Path | None, tier: str) -> Path:
    workspace = path or Path(tempfile.mkdtemp(prefix="dream-run-"))
    workspace.mkdir(parents=True, exist_ok=True)
    if not (workspace / ".git").exists():
        def git(*a: str) -> None:
            subprocess.run(["git", *a], cwd=workspace, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "runner@dream.local")
        git("config", "user.name", "dream-runner")
        git("commit", "--allow-empty", "-q", "-m", "init")
    sandbox = workspace / ".harness" / "sandbox.toml"
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    sandbox.write_text(f'tier = "{tier}"\n', encoding="utf-8")
    return workspace


def skill_name_from(skill_md: Path) -> str:
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("name:"):
            return line.split(":", 1)[1].strip()
    return skill_md.parent.name if skill_md.name == "SKILL.md" else skill_md.stem


def install_inputs(workspace: Path, args: argparse.Namespace) -> None:
    for skill in args.skill:
        skill_md = skill / "SKILL.md" if skill.is_dir() else skill
        if not skill_md.exists():
            raise SystemExit(f"--skill: no SKILL.md at {skill}")
        dest = workspace / "docs" / "skills" / skill_name_from(skill_md)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(skill_md, dest / "SKILL.md")
        print(f"[setup] skill installed: {dest.relative_to(workspace)}")

    if args.memory_file:
        # Seeded under DREAM_HOME, keyed by this workspace.
        from dream.config.paths import DreamPaths
        from dream.memory import project_memory_dir
        paths = DreamPaths.resolve(workspace, env=os.environ).ensure()
        memory_dir = project_memory_dir(paths.home, workspace)
        memory_dir.mkdir(parents=True, exist_ok=True)
        for record in args.memory_file:
            shutil.copy(record, memory_dir / record.name)
            print(f"[setup] memory seeded: {record.name} -> {memory_dir}")

    if args.plugin:
        enabled_lines = []
        for plugin_dir in args.plugin:
            if not (plugin_dir / "manifest.toml").exists():
                raise SystemExit(f"--plugin: no manifest.toml in {plugin_dir}")
            dest = workspace / "plugins" / plugin_dir.name
            shutil.copytree(plugin_dir, dest, dirs_exist_ok=True)
            enabled_lines.append(f'[[plugin]]\nname = "{plugin_dir.name}"\n')
            print(f"[setup] plugin installed: {plugin_dir.name}")
        enabled = workspace / ".harness" / "plugins-enabled.toml"
        enabled.write_text("".join(enabled_lines), encoding="utf-8")

    if args.mcp_allowlist:
        dest = workspace / ".harness" / "mcp-allowlist.toml"
        shutil.copy(args.mcp_allowlist, dest)
        print(f"[setup] MCP allowlist installed: {dest.relative_to(workspace)}")


async def run(args: argparse.Namespace) -> int:
    creds = resolve_creds(args)
    workspace = ensure_workspace(args.workspace, args.sandbox_tier)
    install_inputs(workspace, args)
    print(f"[run] workspace={workspace} tier={args.sandbox_tier} "
          f"skills={not args.no_skills} memory={not args.no_memory} "
          f"mcp={not args.no_mcp} plugins={not args.no_plugins}\n")

    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=workspace,
        max_turns=args.max_turns,
        skills=not args.no_skills,
        memory=not args.no_memory,
        working_memory=args.working_memory,
        mcp=not args.no_mcp,
        plugins=not args.no_plugins,
        wake_model=args.wake_model,
    )
    async with harness:
        result = await harness.run_task(
            intent=args.intent,
            observer=StdioObserver(sys.stdout),
            max_sprints=args.max_sprints,
        )

    print("\n" + "=" * 60)
    print(f"task {result.task_id}: {len(result.sprints)} sprint(s)")
    blocked = False
    for step in result.final_ledger.steps:
        line = f"  step {step.id}: {step.status}"
        if step.status == "blocked":
            blocked = True
            line += f"  (notes: {step.notes[-200:]})" if step.notes else ""
        print(line)
    print(f"spec:   {result.spec_path}\nledger: {result.ledger_path}")
    print("=" * 60)
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
