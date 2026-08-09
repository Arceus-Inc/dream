# dream SDK guide

How to build agents on `dream`: every component, the task loop, the security
model, and the conventions the harness reads from your workspace.

The public API is exactly what `dream/__init__.py` re-exports (pinned by
`tests/test_public_api.py`). Anything not exported there is private and may
change without notice. Current version: `0.1.0`.

---

## 1. Mental model

`dream` is a **runtime, not an application**. You bring:

- a model endpoint (any OpenAI-compatible chat API),
- a git workspace,
- optionally: skills, memories, plugins, MCP servers, hooks, custom tools.

`build_harness()` assembles those into a `Harness`; `harness.run_task()` runs
a complete planner → sprint → evaluator loop inside it. Business logic — which
agents exist, what they work on, how they coordinate — lives in **your** repo;
the harness supplies mechanism, never policy.

```
build_harness(...)            # sync, cheap, no I/O to your model
        │
        ▼
   Harness ──── start_session / run_role     (single role sessions)
        │  └─── run_task(intent=...)         (the full task loop)
        │
   first session start ──► async opener runs ONCE:
                           MCP connect + plugin load (teardown on aclose)
```

## 2. `build_harness` reference

```python
def build_harness(
    *,
    model: str,                      # model / deployment name
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    working_dir: Path,               # your git workspace
    max_turns: int = 8,              # per-session turn budget
    registry: ToolRegistry | None = None,      # bring your own tool registry
    skill_registry: SkillRegistry | None = None,
    skills: bool = True,             # auto-discover workspace SKILL.md files
    memory: bool = True,             # workspace memory + memory_* tools
    working_memory: bool = False,    # opt-in task scratchpad + memory_propose seam
    tasks: bool = False,             # background task_* tools
    cron: bool = False,              # cron_* + remote_trigger
    web: bool = False,               # web_search / web_fetch
    browser: bool = False,           # browser_run (CDP)
    observability: bool = True,      # query_logs / query_metrics (default on)
    worktree: bool = False,          # enter/exit_worktree
    code_intel: bool = False,        # lsp + execute_code
    plan: bool = False,              # plan_show
    legacy_surface: bool = False,    # register every former default pack
    mcp: bool = True,                # connect .harness/mcp-allowlist.toml
    plugins: bool = True,            # load .harness/plugins-enabled.toml
    subagents: SubagentSet | None = None,  # opt-in ephemeral teammates via spawn_subagent
    skill_event_sink=None,           # callback when a skill body loads
    policy_warning_sink=None,        # operator-facing policy warnings
    env: Mapping[str, str] | None = None,      # host resolution only, never creds
    wake_model: str | None = None,   # cheaper model for wake heartbeats
) -> Harness
```

The default registry contains the Level-2 coding tools. Enable additional
capabilities with their pack flag, such as `web=True` for `web_search`,
`web_extract`, and `web_fetch`. Existing callers that rely on the former
full default surface can use `legacy_surface=True` while migrating.

Notes:

- Default tool surface is the **Level-2 coding set** (10 tools). Opt into packs
  with the flags above, or pass `legacy_surface=True` during migration.
- Per-repo tools from `.harness/tools/*.toml` load at construction (invalid
  declarations raise).
- Construction never raises on a bad skill / plugin / allowlist — degraded
  surfaces are skipped or surfaced as findings, the harness still builds
  (except invalid per-repo tool TOML, which blocks).
- MCP connect and plugin import are async I/O, so they run **once, lazily**
  on the first `start_session` (every path funnels through it, including
  `run_task`), and the MCP manager is closed on `aclose()` / `async with` exit.
- Tools registered on the shared `registry` *after* `build_harness` but before
  a session starts are picked up — the wire schema is computed per session.
- Org/employee tools (Arceus MCP `arceus_*`) are not dream builtins — admit
  them via the MCP allowlist from chorus / `@arceus/mcp`.

## 3. The task loop (`run_task`)

```python
result = await harness.run_task(
    intent="...",                    # required
    task_id=None,                    # minted t-YYYYMMDDTHHMMSS-XXXX if omitted
    max_sprints=10,
    observer=StdioObserver(sys.stdout),
    # every LLM head is overridable (see §8):
    planner=None, generator_execute=None, evaluator_run=None,
)
```

**Phases.**

1. **Planner** runs once and commits two artifacts to the workspace: a spec
   (markdown) and a ledger (JSON step list) under `docs/exec-plans/active/`.
   Every step carries its own acceptance criteria — the planner names the work
   and the bar for it in one pass.
2. **Sprint loop**, bounded by `max_sprints`. Each sprint:
   - claims the next `pending` step (or resumes a `needs-changes` one);
   - if the evaluator is enabled, commits a **sprint contract** built from the
     step's criteria plus anything the last evaluation left unresolved;
   - the **generator** executes the step with real tools;
   - the **evaluator** judges the work against the contract and the outcome
     maps onto the ledger: `pass` → `done`, `fail` → `blocked`,
     `needs-changes` → retry next sprint.
3. Ends when no pending steps remain, or `max_sprints` is exhausted.

**Resilience semantics** (built in, nothing to configure):

- *Self-healing heads*: when a head's reply fails to parse (bad ledger JSON,
  missing envelope), the head re-prompts the model with the parse error and
  its previous reply — up to 3 attempts — before raising. Each retry emits a
  `head.retry` observer event.
- *Informed retries*: on `needs-changes` the evaluator's notes are appended to
  the step (`[evaluator, sprint N] ...`) and rendered into the generator's
  next prompt — retries see *why* the last attempt failed.
- *N-strikes escalation*: a step rejected twice escalates to `blocked` (with
  the accumulated notes as the readable reason) instead of burning the sprint
  budget; the runner emits `sprint.escalated`.

**Result.** `RunTaskResult`: `task_id`, `spec_path`, `ledger_path`,
`final_ledger` (step statuses + notes), and per-sprint `SprintRunResult`s
(`sprint_number`, `step_id`, `contract_path`, `eval_path`, `outcome`).

**Roles and their tools.** The planner and evaluator run with a read-only
tool triplet (no shell, no writers); the generator gets every registered tool,
intersected with the sandbox tier. Role enforcement is a hard deny in the
dispatcher — a role cannot reach a tool outside its manifest even if the
permission gate would allow it.

## 4. Observability

Pass any object with `on_event(dict)` as `observer`. `StdioObserver` renders
a live transcript. Event kinds include: `task.started`, `planner.started/
completed`, `sprint.started`, contract events, generator/evaluator session
open/close with streamed text + tool calls, `head.retry`, `sprint.escalated`,
`task.completed`. Every role session also writes an OTel-shaped JSONL trace
under the dream home (`DREAM_HOME`, default `~/.dream`).

## 5. Components

### Skills (progressive disclosure)

Drop playbooks in the workspace:

```
docs/skills/<name>/SKILL.md
---
name: my-rule
description: one-liner shown in the catalogue
when_to_use: when the agent should reach for it
---
# Full playbook body — loaded only on demand
```

The frontmatter catalogue is injected into every session's system prompt; the
agent loads a body with the `skill` tool only when relevant, so prompt cost
stays flat as your skill library grows. `skills=False` disables; passing your
own `skill_registry` overrides discovery.

### Workspace memory (read side)

Durable per-project facts live outside the repo, under the dream home:

```python
from dream.config.paths import DreamPaths
from dream.memory import project_memory_dir

paths = DreamPaths.resolve(working_dir, env=os.environ).ensure()
mem_dir = project_memory_dir(paths.home, working_dir)   # markdown records here
```

Records are markdown with `name`/`description` frontmatter. Their catalogue
goes into the system prompt; the agent pulls full records with
`memory_search` / `memory_get`. Writing memories is **deliberately your
repo's job** (curation is policy, not mechanism).

### Working memory — the task-memory tier (opt-in)

The read side above is the *durable* store (employee/team/company facts your repo
curates). Separately, `build_harness(working_memory=True)` opts the agent into
its **own task scratchpad** — dream's one and only memory tier (spec 11a). It adds
four `safe`/tier-0 tools and a per-session `TaskMemoryContext`:

| Tool | Use |
|---|---|
| `working_memory_read` | Read the task scratchpad — what the agent figured out, open questions, things to remember later in *this* task. |
| `working_memory_write` | Replace the scratchpad wholesale. |
| `working_memory_append` | Append one note line. |
| `memory_propose` | **Outbound seam:** nominate a durable fact (`slug` + `content` + `rationale`) for promotion. Queued for review — *not* applied now. |

Two storage locations, two lifetimes:

- The scratchpad is a single `working-memory.md` under the **task sidecar**
  (`.dream/sidecars/{session-id}/`). It **lives and dies with the worktree** —
  it is the agent's mid-task cognition, not a durable record. Capped at 50 KB
  with optional in-place compression (you may inject a `Compressor`).
- `memory_propose` writes to a durable **`_proposals/` queue under the dream
  home** (`~/.dream`), which **survives worktree teardown**.

The boundary is deliberate: **dream proposes, never promotes.** A task can
*nominate* a fact worth remembering across tasks, but dream never decides its
fate — promotion is a higher clock (employee/team/company) that belongs to your
repo (`lattice`/`chorus`/`horizon` under Model A). Your repo drains the queue and
applies a `MemoryWriter`:

```python
from dream.memory import proposals_dir

# where the agent's proposals land — your curation loop reads these
queue = proposals_dir(paths.home, working_dir)   # ~/.dream/.../_proposals/
for proposal in queue.glob("*.md"):
    ...  # review → promote into the durable store, or discard
```

Default-off keeps the standard tool surface byte-identical; pass
`working_memory=True` to turn it on. See `examples/09_working_memory.py`.

### Subagents (ephemeral teammates, opt-in)

`build_harness(subagents=...)` wires a `SubagentSet` — declared subagent templates
the parent agent dispatches via the `spawn_subagent` tool. Each subagent is a
capability-minimized, ephemeral teammate that runs bounded work and dissolves.

```python
from dream.subagents import Subagent, SubagentRegistry, SubagentSet
from dream.subagents._projection import build_subagent_set

# Tier-1: role-owned specialist
reviewer = Subagent(
    name="reviewer",
    description="Reviews code changes for correctness and style",
    tools=("read_file", "grep", "bash", "git"),   # must be ⊆ parent's tools
    depth=1,                                        # v1: always 1 (flat)
    max_turns=6,
)

# Tier-2: shared capability agent (registered in kernel-level registry)
researcher = Subagent(
    name="researcher",
    description="Researches a topic using codebase search and reasoning",
    tools=("read_file", "grep", "bash"),
    depth=1,
    model="gpt-4o-mini",       # optional cheaper model
    max_turns=4,
)
registry = SubagentRegistry()
registry.register(researcher)

# Build the resolved set for one beat
agent_set = build_subagent_set(
    tier1_agents=[reviewer],
    tier2_agents=registry.resolve(("researcher",)),
)

harness = build_harness(..., subagents=agent_set)
```

The agent dispatches via tool call:

```python
# Inside a beat, the model calls:
spawn_subagent(name="reviewer", prompt="Review the auth change for edge cases")
# → SubagentResult: the subagent's plain-text output, joined into the parent turn
```

**Capability minimization (narrower-wins):** a subagent's tools = parent ∩ declared.
Permission overlays can only tighten, never widen. `allow_permission_prompts=False`
always — fail-closed. V1 is flat (depth 1 only): subagents are leaves and cannot
themselves spawn. Per-beat spawn cap of 10.

**Observability:** `subagent.spawn` and `subagent.complete` trace events land in the
OTel-shaped JSONL alongside `llm.call`/`tool.call` events. Query with `query_logs`.

Default-off keeps the standard tool surface byte-identical; pass `subagents=...` to
turn it on. See `examples/10_subagents.py`.

### Plugins (repo-local extensions)

```
plugins/<name>/manifest.toml      # name, version, entry, [capabilities] required=[...]
plugins/<name>/main.py            # def get_plugin(manifest) -> Plugin
.harness/plugins-enabled.toml     # [[plugin]] name = "<name>"  (opt-in!)
```

A plugin contributes tools / hooks / providers (`dream.contracts.plugin.Plugin`).
Loading is opt-in, capability-gated against the sandbox tier (a plugin
requiring `network` won't load at `repo-write`), wrapped in an init timeout,
and one plugin's failure never aborts the others. Plugin tools enter the
registry as *discovered* — they ride the trust ramp (§7).

### MCP servers

```toml
# .harness/mcp-allowlist.toml — the admission authority
[[mcp]]
name      = "playwright"
endpoint  = "stdio://npx -y @playwright/mcp@latest --headless --isolated"
transport = "stdio"          # stdio | http | ws
```

On first session: read allowlist → admit → connect → register tools as
`mcp__<server>__<tool>`. Credentials, when a server needs them, live in
`.harness/mcp-credentials.toml`. MCP tools are discovered/untrusted until an
operator promotes them (§7). A missing or empty allowlist is a clean no-op.

### Sandbox + shell

The `bash` tool executes through a `SandboxAdapter` (subprocess backend today;
docker is a gated seam), confined to the workspace — absolute paths and `..`
escapes are refused before execution. The active tier comes from:

```toml
# .harness/sandbox.toml
tier = "repo-write"   # read-only | repo-write | repo-write+net-allowlist | unrestricted
```

### Hooks (observer-only)

```python
from dream import HookEvent, HookResult, HookSpec

class AuditHook:
    spec = HookSpec(events=(HookEvent.SESSION_START, HookEvent.PRE_TOOL_USE,
                            HookEvent.POST_TOOL_USE, HookEvent.STOP))
    async def __call__(self, event, payload) -> HookResult:
        ...  # log, meter, mirror — but never veto
        return HookResult()

harness.register_hook(AuditHook())   # works even after build_harness
```

Hooks fire inside the engine loop (PRE/POST strictly bracket each tool
dispatch; the tool-call atom is never broken). They are deadline-bounded and
crash-isolated: a raising hook cannot break a turn. By design hooks **observe,
never block** — vetoes belong to the permission gate.

## 6. Workspace conventions (everything the harness reads)

| Path | Purpose |
|---|---|
| `.harness/sandbox.toml` | sandbox tier |
| `.harness/mcp-allowlist.toml` | MCP admission authority |
| `.harness/mcp-credentials.toml` | MCP credentials (guarded — agents cannot read it) |
| `.harness/plugins-enabled.toml` | plugin opt-in list |
| `.harness/tool-tier-overrides.toml` | trust-ramp promotions |
| `docs/skills/*/SKILL.md` | workspace skills |
| `plugins/<name>/` | repo-local plugins |
| `docs/exec-plans/active/` | task specs, ledgers, sprint contracts (written by the loop) |
| `.dream/` | cron registry, locks, coordination state (written by the loop) |
| `$DREAM_HOME` (default `~/.dream`) | task sidecars, traces, project memory |

## 7. Security model in one minute

- **Tiers** order capability: `read-only < repo-write <
  repo-write+net-allowlist < unrestricted`. The workspace tier caps everything.
- **Declared vs trusted**: built-in tools run at their declared tier.
  *Discovered* tools (plugins, MCP) are treated as `read-only` regardless of
  what they claim, until promoted:

```toml
# .harness/tool-tier-overrides.toml
["mcp__playwright__browser_navigate"]
tier_required = "repo-write"
promoted_by   = "you"
reason        = "browser automation for QA flows"
```

- **Role manifests** then intersect: planner/evaluator are read-only
  regardless of tier; the generator gets the full permitted set.
- **Credential guard**: `.harness/*credentials*` and tier-override files are
  unreadable/unwritable by agent tools — no self-promotion.

## 8. Custom heads, single roles, raw sessions

Every LLM head of the loop is a plain async callable you can replace —
useful for deterministic evaluators, recorded planners, or cheaper models on
a head-by-head basis:

```python
async def my_evaluator(task_id, sprint_number, contract, step) -> EvaluationRecord: ...
await harness.run_task(intent=..., evaluator_run=my_evaluator)
```

Below `run_task` you also have:

- `await harness.run_role("planner" | "generator" | "evaluator", prompt)` —
  one role session with that role's manifest;
- `await harness.start_session(SessionOptions(...))` — a raw engine session
  (per-session `system_prompt` / `model` / `max_turns` overrides).

### Resuming a session in another process

The harness keeps its own transcript of record under
`DreamPaths.sessions_dir` (`~/.dream/data/sessions/{id}.json`, `DREAM_HOME`
honoured). If you are scheduling work in short windows — a control plane that
wakes an agent, lets it run, and exits — persist the returned handle against
whatever key you already have for the work, and resume through it:

```python
session = await harness.start_session(opts, session_id=f"task-{task_id}")
...                                        # send / stream events
handle = await harness.save_session(session)

# Store handle.session_id (and handle.usage_delta for per-run billing).
# Next window:
try:
    session = await harness.resume_session(stored_id)
except SessionResumeError as exc:
    if not exc.should_clear_handle:
        raise                                      # intact elsewhere; not yours to replace
    await harness.reset_session(stored_id)         # spent; frees the name
    session = await harness.start_session(opts, session_id=stored_id)
```

`start_session` refuses an id that already names a saved snapshot, so two
callers picking the same key get an error instead of quietly saving over each
other. Clearing it first is how you say you meant to.

`run_role` takes the same argument, so a role thread continues across beats
without the caller touching sessions at all:

```python
result = await harness.run_role("generator", intent, session_id=f"task-{task_id}-generator")
handle = result.session_handle          # None when session_id is omitted
```

A spent snapshot there (never written, or corrupt) starts the thread over under
the same name rather than failing the run, so one stable key per thread is all
a caller keeps. A snapshot from another working directory is not spent: the run
gets a fresh unnamed session and `session_handle` comes back `None`, leaving
that transcript resumable from the workspace it belongs to.

For a whole task, `run_task` takes a scope instead of a session id and gives
each role its own thread beneath it:

```python
await harness.run_task(intent=intent, session_scope=f"task-{task_id}")
# threads: task-42-planner, task-42-generator, task-42-evaluator
```

Call it again with the same scope and those conversations continue. Each role
has one thread — a planner and an evaluator are different conversations and
never share. Use `dream.runner.role_session_id(scope, role)` to address one
thread directly, for example to `reset_session` just the generator.

Keep only the handle, not a second copy of the transcript. `usage_delta`
covers the work since the previous save, so you never have to difference
cumulative totals. A resume whose snapshot was taken under a different working
directory raises `working_dir_mismatch` rather than replaying a transcript
about other files; pass `allow_working_dir_change=True` when that is what you
want. Give each concurrent agent its own `DREAM_HOME` (or an explicit
`FileSessionStore`) so their session roots stay isolated.

## 9. Always-on agents

For long-running agents, wrap the harness in the runtime:

```python
from dream import Runtime, RuntimeConfig

async with Runtime(harness, RuntimeConfig(...)) as rt:
    await rt.run_forever()      # wake scheduler + cron + control plane
```

- `python -m dream.daemon` runs it as a process; `python -m dream.ctl` is the
  operator CLI; commands arrive via the `dream.channels` drop-dir.
- Cron jobs (`.harness/cron/*.toml`) wake the agent on schedule;
  `wake_model=` lets heartbeats run on a cheap model.
- `dream.tail_events` / the events JSONL are the monitoring surface.

## 10. Known limits (v0.1.0)

- Cost accounting (`SessionCost`) is not wired for all gateways; no per-task
  token/$ budgets yet.
- A step's acceptance criteria are fixed at plan time; only a `needs-changes`
  verdict can add to them.
- Plugin-contributed *skills* don't join the prompt catalogue yet (their
  tools/hooks/providers do).
- Docker sandbox backend is a refusing seam — subprocess only today.
