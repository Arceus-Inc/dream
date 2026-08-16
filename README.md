<div align="center">

# dream

### An SDK for building **autonomous agent harnesses**.

*Construct a `Harness`, stream typed events, and drive tasks to verified completion.*

![python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![tests](https://img.shields.io/badge/tests-2366%20passing-2ea44f)
![ci](https://github.com/Arceus-Inc/dream/actions/workflows/ci.yml/badge.svg)
![typing](https://img.shields.io/badge/mypy-strict-1f6feb)
![license](https://img.shields.io/badge/license-MIT-blue)
![stack](https://img.shields.io/badge/Arceus-runtime%20layer-8A2BE2)

</div>

---

`dream` is the **runtime layer** of the [Arceus](https://github.com/Arceus-Inc) stack: a pure-Python SDK that owns the agent loop, tool surface, permissions, sandboxes, providers, memory, MCP, plugins, hooks, and the **plan → sprint → evaluate** task loop.

It deliberately **knows nothing** about employees, companies, channels, or strategy — those live in sibling repos (`chorus`, `horizon`, `lattice`). dream runs the agent; your product policy lives on top.

**Scale (this repo):** ~238 Python modules under `src/dream/`, ~188 test modules, **2366** tests, **52** public symbols pinned in [`tests/test_public_api.py`](tests/test_public_api.py).

## Install

```bash
pip install dream
pip install "dream[anthropic,openai,docker]"   # optional providers + Docker sandbox
```

Python **3.11+**. Development uses [`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Arceus-Inc/dream.git && cd dream
uv sync --all-extras
```

Copy [`.env.example`](.env.example) → `.env.local` for smoke credentials (`DREAM_SMOKE_*`, optional `DREAM_TAVILY_API_KEY` for `web_search`).

---

## Two ways to use it

### 1. One conversation (`Session`)

For chat-style agents: one model session, typed event stream, tool loop.

```python
import asyncio
from pathlib import Path
from dream import build_harness

async def main() -> None:
    harness = build_harness(
        model="gpt-4.1",
        api_key="...",                              # always explicit — never implicit env reads
        base_url="https://api.openai.com/v1",       # any OpenAI-compatible endpoint
        working_dir=Path("./my-workspace"),         # git repo recommended
    )
    async with harness:
        session = await harness.start_session()
        async for event in session.send("write a haiku about long-running agents"):
            print(event)                            # TextDelta, ToolUseStart, ToolUseResult, …
        print(session.cost)

asyncio.run(main())
```

Every consumer-visible output is a typed [`Event`](src/dream/events.py) — `TextDelta`, `ToolUseStart`, `ToolUseResult`, `TurnComplete`, `PermissionDenied`, `HookBlocked`, `Compacted`, `Error`. No hidden logging side effects.

### 2. One verified task (`run_task`)

For autonomous work: planner commits a spec + step ledger, then bounded **sprint → evaluate** cycles until done, blocked, or cancelled.

```python
import asyncio
from pathlib import Path
from dream import build_harness

async def main() -> None:
    harness = build_harness(
        model="gpt-4.1",
        api_key="...",
        base_url="https://api.openai.com/v1",
        working_dir=Path("./my-workspace"),
    )
    async with harness:
        result = await harness.run_task(
            intent="Add a CLI that prints today's date and a unit test for it",
        )
        print(result.passed, result.final_text)

asyncio.run(main())
```

Failures surface as typed exceptions: [`RunTaskError`](src/dream/errors.py) (names the broken phase: `plan` / `sprint` / `evaluate`) and [`TaskCancelled`](src/dream/errors.py). State is durable on disk under `docs/exec-plans/active/` and `.dream/` sidecars so tasks are crash-safe and resumable.

You can also run a **single role** (`run_role`) — planner, generator, or evaluator — with a [`RoleManifest`](src/dream/roles/_manifest.py) that hard-limits tools.

---

## Does it actually work?

25 tasks from **SWE-bench Lite**, run inside each task's official evaluation container, graded
by the official SWE-bench Docker harness. Same model (`gpt-5.2`), same prompt, against
[opencode](https://github.com/anomalyco/opencode) as a baseline:

| | dream | opencode |
|---|---|---|
| resolved | **19/25 (76%)** | 21/25 (84%) |
| median time / task | 122 s | 98 s |
| median agent steps | 1 sprint | 10 steps |
| harness errors | 0 | 0 |

Two tasks apart at n=25 is noise — the interesting result is *how* the two differ, and one of
them is a bug in this repo: `dream`'s failures cost ~2.4× its successes because it has no
cheap-abort signal, and one run reported success while producing an empty diff.

The write-up keeps the negative results: **[docs/learnings/2026-07-26-swe-bench-lite-vs-opencode.md](docs/learnings/2026-07-26-swe-bench-lite-vs-opencode.md)**.
The rig is checked in and reproducible: **[datasets/swe-bench-lite/](datasets/swe-bench-lite/)**.

> The benchmark is **oracle-assisted** — both agents are given the acceptance test command, so
> these rates are far above public SWE-bench leaderboards and are not comparable to them. Only
> the harness-vs-harness comparison is meaningful.

Notably, driving this required **zero changes to `src/dream/`** — `build_default_harness()` +
`run_task()` was the entire integration.

---

## What's inside

| Layer | Packages / entrypoints | What it does |
|-------|------------------------|--------------|
| **Facade** | [`harness.py`](src/dream/harness.py), [`_factory.py`](src/dream/_factory.py) | `build_harness()` wires streamer, tools, permissions, skills, memory, sandbox, MCP, plugins, cron registry, compaction |
| **Engine** | [`engine/`](src/dream/engine/) | Async turn loop, tool dispatch, permission gate, OpenAI-compatible streaming, auto-compaction |
| **Task loop** | [`runner/`](src/dream/runner/), [`planner/`](src/dream/planner/), [`sprint/`](src/dream/sprint/) | `run_task`: five overridable LLM heads, sprint contracts, evaluation records (`pass` / `needs-changes` / `fail`) |
| **Tools** | [`tools/builtin/`](src/dream/tools/builtin/) | ~30 default tools (files, shell, git, tasks, cron, web, memory, observability) + custom `BaseTool` + MCP adapters |
| **Roles** | [`roles/`](src/dream/roles/) | Planner (read-only), generator (full surface ∩ sandbox), evaluator (read-only) |
| **Subagents** | [`subagents/`](src/dream/subagents/) | Opt-in `spawn_subagent` — capability-minimized ephemeral teammates (depth-2, spawn cap) |
| **Security** | [`permissions/`](src/dream/permissions/), [`sandbox/`](src/dream/sandbox/) | Tiered sandbox (`read-only` → `unrestricted`), trust ramp for discovered tools, subprocess + Docker backends |
| **Extensions** | [`skills/`](src/dream/skills/), [`plugins/`](src/dream/plugins/), [`hooks/`](src/dream/hooks/), [`mcp/`](src/dream/mcp/) | Progressive-disclosure skills, repo plugins, lifecycle hooks, MCP allowlist + credentials |
| **Memory** | [`memory/`](src/dream/memory/) | Project memory catalogue + search/get; opt-in task scratchpad (`working_memory=True`) + `memory_propose` outbound queue |
| **Background work** | [`tasks/`](src/dream/tasks/), [`services/cron.py`](src/dream/services/cron.py) | Background shell tasks, cron manifests under `.harness/cron/` (drive with your scheduler or `--once` scripts) |
| **Observability** | [`observability/`](src/dream/observability/), [`runner/_observer.py`](src/dream/runner/_observer.py) | OTel-shaped JSONL traces plus default-on OTLP (`OTEL_SDK_DISABLED=true` to keep JSONL only), `tail_events`, macro run observer for `run_task` |
| **Repo contract** | [`services/repo_validator.py`](src/dream/services/repo_validator.py), [`config/paths.py`](src/dream/config/paths.py) | Session-start validator: `AGENTS.md`, required `docs/` tree, link + JSON schema checks |
| **Contracts** | [`contracts/`](src/dream/contracts/) | Zero-dep Protocols for siblings — `__contract_version__ = "0.4.0"` |

Full concept map with every tool documented: **[consumer-facing-api/HARNESS.md](consumer-facing-api/HARNESS.md)**.

### Default built-in tools (high level)

Registered by [`default_registry()`](src/dream/tools/builtin/__init__.py):

- **Workspace:** `read_file`, `apply_patch`, `write_file`, `bash`, `git`, `glob`, `grep`, `lsp`, `read_offloaded`
- **Knowledge:** `skill`, `memory_search`, `memory_get`
- **Tasks & schedule:** `task_create/get/output/stop/update`, `cron_list/show/create/delete/toggle`, `plan_show`, `todo_write`, `remote_trigger`
- **Isolation:** `enter_worktree`, `exit_worktree`
- **Web (tier-2):** `web_search` (Tavily; needs API key), `web_fetch` (direct GET)
- **Self-observability:** `query_logs`, `query_metrics`

**Opt-in surfaces:** `working_memory_*` + `memory_propose` (`working_memory=True`); `spawn_subagent` (`subagents=SubagentSet(...)`); dynamic `mcp__*` tools after MCP connect.

---

## Cross-repo contracts

Sibling repos import **`dream.contracts` only** (no `httpx`, no providers). Shipped seams include:

| Contract | Purpose |
|----------|---------|
| `Tool`, `Hook`, `Skill`, `Plugin`, `Provider`, `Memory*` | Extension shapes |
| `ExecPlan`, `ExecPlanLedger`, `ExecPlanStatus` | Task planning artefacts |
| `GoalStore`, `IntakePort`, `OutcomeFeed`, `OutcomeEvent` | Horizon strategy feedback |
| `GovernancePort`, `Gov*` | CEO reverse edge onto direction |
| `DelegatedIntakePort`, `CapacityPort`, … | Team delegation intake |

Bump `dream.contracts.__contract_version__` on breaking Protocol changes; siblings assert at startup.

---

## Documentation & examples

| Path | Start here if you… |
|------|---------------------|
| [consumer-facing-api/QUICKSTART.md](consumer-facing-api/QUICKSTART.md) | Want zero → `run_task()` in five minutes |
| [consumer-facing-api/SDK_GUIDE.md](consumer-facing-api/SDK_GUIDE.md) | Need the full SDK + security model |
| [consumer-facing-api/HARNESS.md](consumer-facing-api/HARNESS.md) | Want every harness concept mapped to code |
| [consumer-facing-api/examples/](consumer-facing-api/examples/) | Prefer runnable scripts (01–11: skills, memory, MCP, hooks, subagents, …) |
| [examples/run_task_demo.py](examples/run_task_demo.py) | Want live stdio walkthrough of the full task loop |
| [examples/research_claw/](examples/research_claw/) | Want a cron-friendly `--once` agent with oracle verification |
| [datasets/swe-bench-verified-100/](datasets/swe-bench-verified-100/) | Need 100 real coding tasks + gold patches for eval |
| [datasets/swe-bench-lite/](datasets/swe-bench-lite/) | Want to reproduce the benchmark, or point it at another harness |
| [docs/learnings/](docs/learnings/) | Want measured results and the failure modes they exposed |
| [docs/specs/divo/](docs/specs/divo/) | Are implementing against the build-order specs (00–15) |
| [AGENTS.md](AGENTS.md) | Are a coding agent navigating this repo |

CLI runner toggling every harness knob:

```bash
uv run python consumer-facing-api/run_harness.py \
  --intent "Create hello.py" --workspace /tmp/demo --no-mcp
```

---

## Repository layout

```
src/dream/
  __init__.py          # public API (52 symbols, pinned by tests)
  harness.py           # Harness facade: sessions, run_role, run_task
  _factory.py          # build_harness() — the one wiring factory
  session.py           # one conversation
  events.py            # typed consumer event stream
  errors.py            # RunTaskError, TaskCancelled, …
  contracts/           # cross-repo Protocols (zero runtime deps)
  engine/              # private turn loop + tool dispatch
  api/                 # OpenAI + Anthropic provider adapters, credentials
  runner/ planner/ sprint/   # run_task orchestration
  tools/ roles/ subagents/   # action surface + role manifests
  permissions/ sandbox/        # capability tiers + isolation
  skills/ plugins/ hooks/ mcp/
  memory/ tasks/ services/     # memory tiers, cron, compaction, repo validator
  observability/ state/ utils/
tests/                 # mirrors src/dream (~2366 tests)
consumer-facing-api/   # guides + runnable examples
examples/              # run_task_demo, research_claw
docs/                  # specs, design docs, learnings, runtime-events catalogue
datasets/              # eval task packs + the SWE-bench benchmark rig
```

### The Arceus stack

```
dream      one task     →  agent loop: plan → sprint → evaluate          ← this repo
chorus     one sprint   →  org of employees doing durable work
horizon    one company  →  strategy / OKRs / direction
lattice    the people   →  employee growth + memory consolidation
```

Strict bottom-up: siblings depend on **dream**; dream depends on none of them.

---

## Design rules

From [`docs/design-docs/core-beliefs.md`](docs/design-docs/core-beliefs.md):

1. **One facade: `Harness`.** Many instances per process. No globals.
2. **Constructor injection.** Credentials and paths are always explicit at `build_harness()`; env helpers are opt-in.
3. **Async-first.** Primary API is `async`; use `asyncio.run` or your own event loop.
4. **Typed events only.** No prints or logging as the consumer contract.
5. **Public API = `dream.__init__.__all__`.** Everything else is private.
6. **Contracts stay dependency-free.** Siblings import `dream.contracts`, not providers.
7. **Fail closed.** Permissions and validators deny by default.
8. **Repo is system of record.** Durable state in git; ephemeral state under `.dream/` (ignored).

This repository satisfies its own session-start validator (spec 01) — CI runs it on every push.

---

## Status

**v0.1.0** (alpha) — `ruff` + `mypy --strict` + 2366 tests on Python 3.11–3.14. See [CHANGELOG.md](CHANGELOG.md).

Alpha means the public API is pinned by tests but not yet frozen; breaking changes land with a
CHANGELOG entry and a `dream.contracts.__contract_version__` bump where relevant.

Known gaps, measured rather than assumed (see [docs/learnings/](docs/learnings/)):

- **No cheap abort.** `run_task` spends its full sprint budget on tasks it cannot solve, making
  failures ~2.4× more expensive than successes.
- **"Verification unavailable" is not distinct from "verification failed."** When the acceptance
  tests cannot run at all, the loop retries instead of stopping.
- **One empty-diff-but-passing case** observed under benchmark conditions.

Long-running daemon composition (`dream.Runtime`, `dream.ctl`) is specified in [docs/specs/divo/15-long-running-runtime.md](docs/specs/divo/15-long-running-runtime.md); today you compose **cron manifests**, **background tasks**, and **`--once` agent scripts** (see `examples/research_claw/`) until that facade lands on `main`.

---

## Open source

MIT licensed.

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, PR checklist, boundary rules
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md) — report vulnerabilities privately
- [AGENTS.md](AGENTS.md) — repo map for humans and coding agents

## Development

```bash
uv sync --all-extras
uv run pytest                  # full suite
uv run ruff check src tests
uv run mypy --strict src
```

## License

MIT — see [LICENSE](LICENSE).
