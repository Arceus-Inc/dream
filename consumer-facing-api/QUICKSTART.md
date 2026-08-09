# Quickstart

From nothing to a live `run_task()` in five minutes.

## 1. Install

`dream` is a Python 3.11+ package. From your consuming repo:

```bash
# path dependency while developing
uv add --editable /path/to/dream
# or: pip install -e /path/to/dream
```

## 2. Credentials

`build_harness` takes explicit credentials — nothing is read from the
environment implicitly. Any OpenAI-compatible chat endpoint works (OpenAI,
Azure's `/openai/v1` path, vLLM, gateways):

```bash
export DREAM_MODEL="gpt-5.2"                 # model / deployment name
export DREAM_API_KEY="sk-..."
export DREAM_BASE_URL="https://api.openai.com/v1"
```

(The examples in this folder read those three variables; your own code can
pass the values from wherever you like.)

## 3. A workspace

The harness operates on a **git repository** — artifacts, task ledgers, and
sprint contracts are committed as the loop runs:

```bash
mkdir my-workspace && cd my-workspace && git init
git commit --allow-empty -m init
```

Optional, but recommended — set the sandbox tier (default is `repo-write`):

```bash
mkdir -p .harness
echo 'tier = "repo-write"' > .harness/sandbox.toml
```

## 4. Run a task

```python
import asyncio
import os
import sys
from pathlib import Path

from dream import build_harness
from dream.runner import StdioObserver


async def main() -> None:
    harness = build_harness(
        model=os.environ["DREAM_MODEL"],
        api_key=os.environ["DREAM_API_KEY"],
        base_url=os.environ["DREAM_BASE_URL"],
        working_dir=Path("./my-workspace"),
    )
    async with harness:
        result = await harness.run_task(
            intent=(
                "Create a Python module greet.py exposing greet(name) that "
                "returns 'Hi ' + name, plus a passing pytest test for it."
            ),
            observer=StdioObserver(sys.stdout),   # stream progress to stdout
            max_sprints=4,
        )
    print(f"task {result.task_id}: {len(result.sprints)} sprint(s)")
    for step in result.final_ledger.steps:
        print(f"  step {step.id}: {step.status}")


asyncio.run(main())
```

What happens when you run it:

1. **Planner** (one LLM role session) writes a spec + a step ledger into the
   workspace under `docs/exec-plans/active/`, with acceptance criteria on
   every step.
2. **Sprint loop** — per sprint: the step's criteria are committed as a sprint
   contract, the generator executes the step with real tools (files, sandboxed
   bash, skills, memory, your plugins/MCP tools), then the evaluator judges the
   result against that contract.
3. The loop ends when every step is `done`, a step is `blocked`, or
   `max_sprints` is reached. Everything is on disk and resumable.

## 5. What you got for free

That one `build_harness` call auto-wired, with no extra configuration:

- **Skills** — any `docs/skills/*/SKILL.md` in the workspace is catalogued in
  the system prompt; the agent loads playbooks on demand with the `skill` tool.
- **Workspace memory (read)** — durable facts under the project memory dir are
  catalogued and readable via `memory_search` / `memory_get`.
- **Sandboxed shell** — the `bash` tool executes through a sandbox adapter,
  confined to the workspace, gated by the tier in `.harness/sandbox.toml`.
- **Permission gate + trust ramp** — built-in tools run at their declared
  tier; discovered tools (plugins, MCP) start untrusted until promoted in
  `.harness/tool-tier-overrides.toml`.
- **MCP** — servers listed in `.harness/mcp-allowlist.toml` are connected on
  first session and their tools registered (see `examples/05_mcp.py`).
- **Plugins** — repo-local plugins enabled in `.harness/plugins-enabled.toml`
  are loaded, tier-gated (see `examples/04_plugins.py`).
- **Resilience** — malformed LLM replies in any orchestration head are
  re-prompted with feedback (up to 3 attempts); a step that keeps failing
  evaluation escalates to `blocked` after 2 strikes instead of burning your
  sprint budget, with the evaluator's feedback recorded on the step.

One surface is **off by default** — opt in with `working_memory=True`:

- **Working memory (task scratchpad)** — the agent gets `working_memory_read/
  write/append` (a `working-memory.md` that lives and dies with the worktree)
  plus `memory_propose`, an outbound seam to nominate durable facts for your
  repo to promote (dream proposes, never promotes). See
  [SDK_GUIDE.md](SDK_GUIDE.md) and `examples/09_working_memory.py`.

Each surface is a boolean parameter:
`build_harness(..., skills=False, memory=False, mcp=False, plugins=False,
working_memory=True)`.

## Next

- Component-by-component examples: [examples/](examples/)
- Everything configurable from one CLI: [run_harness.py](run_harness.py)
- The full guide (components, security model, always-on runtime):
  [SDK_GUIDE.md](SDK_GUIDE.md)
