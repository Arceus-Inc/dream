<div align="center">

# dream

### An SDK for building **autonomous agent harnesses**.

*One `Harness` you construct, configure, and stream typed events from — it owns the agent loop.*

![python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![tests](https://img.shields.io/badge/tests-2765%20passing-2ea44f)
![typing](https://img.shields.io/badge/mypy-strict-1f6feb)
![license](https://img.shields.io/badge/license-MIT-blue)
![stack](https://img.shields.io/badge/Arceus-runtime%20layer-8A2BE2)

</div>

---

`dream` is the **runtime layer** of the [Arceus](https://github.com/Arceus-Inc) stack. You construct a
`Harness`, open a session, and stream typed events as the agent thinks, calls tools, and finishes. It
owns the agent loop, tools, hooks, permissions, sandboxes, providers, sessions, memory, and the
plan → sprint → evaluate task loop.

It deliberately **knows nothing** about employees, companies, channels, or strategy — those live in
`chorus`, `lattice`, and `horizon`. dream just runs the agent.

## Install

```bash
pip install dream
# or, with providers
pip install "dream[anthropic,openai]"
```

Python 3.11+.

## Hello, harness

```python
import asyncio
from dream import Harness, HarnessConfig

async def main() -> None:
    async with Harness(HarnessConfig()) as h:
        session = await h.start_session()
        async for event in session.send("write a haiku about long-running agents"):
            print(event)
        print(session.cost)

asyncio.run(main())
```

Every consumer-visible output is a typed [`Event`](src/dream/events.py) — no prints, no logging side
effects, no guessing. New here? Read the **[Quickstart](consumer-facing-api/QUICKSTART.md)** and the
**[SDK guide](consumer-facing-api/SDK_GUIDE.md)**, then browse the **[examples](examples/)**.

## One task, end to end — the plan → sprint → evaluate loop

Beyond a single conversation, dream runs a whole task to a *verified* result: it **plans**, runs the
**sprint**, and **evaluates** the outcome against the goal. This is the loop `chorus` drives to make a
task somebody's job. The public contract around it is fully typed — an `ExecPlan` (+ `ExecPlanLedger`,
`ExecPlanStatus`) describes the work, and a fault surfaces as a typed **`RunTaskError`** that names the
phase (`plan` / `sprint` / `evaluate`) it broke in, while cancellation propagates as `TaskCancelled`.

---

## What's inside

| Capability | What it gives you |
|---|---|
| **`Harness` + `Session`** | One facade, many instances per process, no globals. A session is one conversation with a typed event stream. |
| **Tools** | Built-in tools + your own; structured `ToolResult`, typed `ToolContext`. |
| **Hooks** | Intercept the turn loop (`HookEvent` → `HookResult`); a hook can block or rewrite. |
| **Permissions** | Capability gating with explicit `PermissionDenied` — fail-closed by default. |
| **Sandboxes** | Run tool side-effects in isolation (local / Docker). |
| **Providers** | Anthropic, OpenAI, and any OpenAI-compatible endpoint behind one `Provider` interface. |
| **Skills & Plugins** | Package reusable capability bundles (`Skill`) and extensions (`Plugin` + `PluginManifest`). |
| **Memory** | Typed memory records, scopes, and a `MemoryWriter` seam for consolidation. |
| **MCP** | Speak the Model Context Protocol to external tool servers. |
| **Tasks** | the plan → sprint → evaluate loop, a typed `ExecPlan`, and a `RunTaskError` failure contract. |
| **`contracts/`** | Zero-dependency cross-repo Protocols, so `chorus`/`lattice`/`horizon` depend on the contract, not the providers. |

---

## Design rules

1. **One facade: `Harness`.** Many instances per process. No globals.
2. **Constructor injection.** Env / file loading is an opt-in helper, never implicit.
3. **Async-first.** The sync facade lives in `dream.sync` and is thin.
4. **All consumer output is a typed `events.Event`.** No prints, no logging side effects.
5. **The public API is exactly what `dream/__init__.py` re-exports** — pinned by
   `tests/test_public_api.py`. Anything not re-exported may change.
6. **Cross-repo contracts live in `dream.contracts`** with zero runtime dependencies, so siblings can
   depend on them without pulling in providers.

---

## Where things live

```
src/dream/
  __init__.py        # the public API surface (56 symbols, pinned by tests)
  harness.py         # the Harness facade
  session.py         # one conversation
  events.py          # the typed event stream
  errors.py          # the exception hierarchy
  contracts/         # cross-repo Protocols (zero deps)
  engine/            # the private turn loop
  api/               # providers (Anthropic, OpenAI, …)
  tools/builtin/     # built-in tools
  skills/  plugins/  hooks/  permissions/  sandbox/
  memory/  mcp/  swarm/  tasks/  services/
  prompts/  config/  state/  utils/
```

### The Arceus stack

```
dream      one task        →  the agent loop: plan → sprint → evaluate     ← this repo
chorus     one sprint      →  the org of employees that do durable work
horizon    one company     →  strategy / OKRs / direction
lattice    the people      →  employee growth + memory consolidation
```

Strict bottom-up: the siblings depend on **dream**; dream depends on none of them.

---

## Status

**v0.1.0** — gated by `ruff` + `mypy --strict` + **2765 passing tests**. See the
[CHANGELOG](CHANGELOG.md) for what's landed.

## Development

```bash
uv sync --all-extras
uv run pytest -q              # the full suite
uv run ruff check .           # lint
uv run mypy --strict src      # types
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
