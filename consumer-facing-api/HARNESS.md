# HARNESS.md — every concept in dream, and what we implemented for each

The single-page map of the harness: each concept defined, what exists in the
codebase for it, and where it lives. Companion to the consumer docs in this
folder ([README](README.md), [QUICKSTART](QUICKSTART.md), [SDK_GUIDE](SDK_GUIDE.md)).

---

## 1. The harness itself

**Concept.** A *harness* is the runtime envelope around a model: it owns the
provider connection, the tool surface, the permission boundary, prompt
assembly, and the orchestration loops. dream is a harness **SDK** — mechanism
only; which agents exist and what they work on is the consuming repo's policy.

**Implemented.**
- `Harness` + typed `HarnessConfig` (`src/dream/harness.py`) — registration
  surface (`register_tool/hook/plugin/provider`), session lifecycle, the
  `run_role` / `run_task` entrypoints.
- `build_harness()` (`src/dream/_factory.py`) — the one public
  construct-and-run factory: streamer + tools + permission gate + skills +
  memory + sandbox + hooks + tasks/cron wired behind explicit parameters
  (`skills/memory/mcp/plugins` booleans). No env-var coupling; credentials
  are always explicit.
- **Async-open chokepoint** — `Harness._ensure_open()`: MCP connect and
  plugin import are async I/O, so they run exactly once at the first
  `start_session` (every path funnels through it), with teardown on
  `aclose()`. Idempotent, lock-guarded.

## 2. Engine, sessions, turns

**Concept.** A *session* is one conversation with the model; a *turn* is one
model reply plus the tool calls it makes. The engine streams a turn, dispatches
tool calls, appends results, and loops until the model stops or budgets run out.
The **tool-call atom** — nothing may be injected between a `tool_use` and its
`tool_result` — is invariant.

**Implemented.**
- `QueryEngine` / `build_query_engine` (`src/dream/engine/_engine.py`),
  session loop (`_session.py`, `_loop.py`), `EngineToolDispatcher`
  (`_tool_dispatch.py`).
- `OpenAIChatStreamer` + `httpx_chat_completion_stream`
  (`_adapter_openai.py`) — any OpenAI-compatible endpoint (OpenAI, Azure
  `/openai/v1`, vLLM, gateways); tool schemas ride `extra_params`.
- Per-session engine construction: every `start_session` builds a fresh
  engine, so per-session `system_prompt`/`model`/`max_turns` overrides and
  late tool registrations (MCP, plugins) take effect.
- **System prompt assembly** (ordered blocks, each survives an empty
  successor): governance standing orders → runtime info (OS/shell/python) →
  skill catalogue → memory catalogue → caller prompt.
- **Auto-compaction** — `AutoCompactState` shared across sessions; context
  utilisation tracked against a 128K default window.

## 3. Roles and manifests

**Concept.** A *role manifest* fixes a role's prompt and allowed toolset. Role
enforcement is a hard deny in the dispatcher — a role cannot reach a tool
outside its manifest even if the permission gate would allow it (and the
manifest is never used to *widen* the gate).

**Implemented.** `RoleManifest` + defaults (`src/dream/roles/`):
- **planner** — read-only triplet, no shell, no writers; drafts, never executes.
- **generator** — `tools=None` = every registered tool, intersected with the
  sandbox tier; the only role that does work.
- **evaluator** — read-only triplet; judges, never fixes.
- `compute_minimum_toolset` / `compute_session_role_allowlist` — manifest ∩
  tier ∩ declared tool tiers, stamped per session.

## 4. The task loop (`run_task`)

**Concept.** A *task* is an intent driven to completion through a
planner-once → bounded-sprint loop. All state is durable on disk, so a task
is crash-safe and resumable.

**Implemented** (`src/dream/runner/_run.py`, `src/dream/planner/`,
`src/dream/sprint/`):
- **Spec + ledger** — the planner commits a markdown spec and a JSON step
  ledger (`docs/exec-plans/active/t-*.{md,json}`). `LedgerStep`:
  `id, description, status (pending|in_progress|done|blocked), notes,
  needs_changes_count`.
- **Sprint** — one claim-execute-evaluate cycle on one step, lock-protected.
- **Sprint contract** — negotiated before execution (evaluator *proposes*
  acceptance criteria, generator *responds* accept/counter), committed to
  `…-sprint-N.json`. The planner can disable the evaluator per-task.
- **Evaluation outcomes** — `pass` → `done`; `fail` → `blocked` (+ tech-debt
  entry); `needs-changes` → retry next sprint.
- **Five overridable LLM heads** — `planner`, `generator_execute`,
  `evaluator_propose`, `generator_respond`, `evaluator_run`
  (`src/dream/runner/_*_head.py`): each is a plain async callable; pass your
  own to `run_task` (deterministic oracles, cheaper models per head).
- **Self-healing heads** (`_head_retry.py`) — `ask_until_parsed`: a malformed
  reply is re-prompted with the parse error + the previous reply, up to 3
  attempts, then the last `*HeadParseError` raises unchanged. Engine errors
  never retry. Emits `head.retry`.
- **Sprint adaptation** (`sprint/_outcome.py`) — on `needs-changes` the
  evaluator's notes append to `step.notes` (`[evaluator, sprint N] …`) so the
  retry prompt is informed; after `NEEDS_CHANGES_LIMIT = 2` strikes the step
  escalates to `blocked` instead of burning `max_sprints`. Emits
  `sprint.escalated`.
- **Oracle verification** — verification steps execute and the evaluator
  judges their evidence; pass requires the oracle green.

## 5. Tools

**Concept.** A tool is a typed capability (`BaseTool`: name, description,
pydantic `input_model`, `ToolDeclaration(risk, tier_required, timeout)`)
dispatched by the engine under the permission gate. *Risk* is honesty about
side effects (`safe` = never mutates, `mutating`, `external`); *tier* is the
minimum sandbox tier required. Errors follow a three-part contract
(`root_cause / safe_retry / stop_condition`) so the model can recover.

### 5a. Default registry (18 tools, `dream.tools.builtin.default_registry`)

| Tool | Risk / tier | Use case |
|---|---|---|
| `read_file` | safe / 0 | Read a repo file (line-numbered, offset/limit) — the agent's eyes. |
| `edit_file` | mutating / 1 | Surgical substring replacement in an existing file. |
| `write_file` | mutating / 1 | Create or overwrite a file — primary artifact producer. |
| `bash` | mutating / 1 | Run a shell command **through the sandbox adapter**, cwd-confined to the workspace; 10-min timeout, tree-kill on expiry. |
| `git` | safe / 0 | Read-only git subcommands (status/diff/log) — lets planner/evaluator inspect history without shell access. |
| `read_offloaded` | safe / 0 | Re-read a large tool result that was offloaded to session scratch instead of bloating context. |
| `skill` | safe / 0 | Load a skill playbook by name — the progressive-disclosure lever (§7). |
| `memory_search` | safe / 0 | Keyword search over workspace memory records (id + description + snippet per hit). |
| `memory_get` | safe / 0 | Load one full memory record by id; unknown id steers back to `memory_search`. |
| `query_logs` | safe / 0 | Query this session's own trace events (labels, substring, time window) — self-observability. |
| `query_metrics` | safe / 0 | Aggregated counters over the same trace (tool counts, error rates). |
| `task_create` | mutating / 1 | Spawn a long-running background command as a managed task (detached, logged) instead of blocking a turn. |
| `task_get` | safe / 0 | Poll a background task's status/return code/timing. |
| `task_output` | safe / 0 | Tail the last N bytes of a background task's log. |
| `task_stop` | mutating / 1 | Stop a background task; idempotent on terminal tasks. |
| `cron_list` | safe / 0 | List configured cron jobs with schedule + next run. |
| `cron_show` | safe / 0 | Full config of one cron job. |
| `plan_show` | safe / 0 | Render the exec-plan (spec + ledger) for a task id — how a session orients mid-task. |

### 5b. Dynamically registered (MCP, on connect)

| Tool | Use case |
|---|---|
| `mcp__<server>__<tool>` | One adapter per admitted MCP tool (`McpToolAdapter`): the server's JSON Schema is synthesized into a pydantic model; calls proxy through the client manager; the server's host is reported as a network effect so the gate can apply the network ceiling. |
| `mcp_auth` | Configure credentials for an MCP server and reconnect it. |
| `list_mcp_resources` / `read_mcp_resource` | Enumerate / read MCP resources (files, docs) exposed by connected servers. |

### 5c. Implemented but not in the default registry

`glob`, `grep`, `todo_write`, `web_fetch` (`src/dream/tools/builtin/`) — ready
for callers that build a custom `ToolRegistry`; kept out of the default set to
keep the wire schema lean. `HeartbeatTool` is the single *virtual* tool
advertised on wake turns (§10) — never part of a work session.

### 5d. Registry & provenance

`ToolRegistry` (`src/dream/tools/_registry.py`) — deterministic ordering,
collision refusal, and a `ToolSource` provenance tag per tool
(`DEFAULT | PER_REPO | SKILL | MCP`). Only `DEFAULT` is trusted at its
declared tier; everything discovered rides the trust ramp (§6).

## 6. Permissions, tiers, trust

**Concept.** Capability is capped by an ordered sandbox tier; trust is
per-tool and operator-granted. The gate runs a full pipeline on every
dispatch; roles narrow further (§3).

**Implemented** (`src/dream/permissions/`, `engine/_permission_gate.py`):
- **Tiers** — `read-only < repo-write < repo-write+net-allowlist <
  unrestricted`, read from `.harness/sandbox.toml`.
- **Gate pipeline** — path-deny, command-deny, tier check, trust check per
  tool call; deny verdicts carry the three-part error contract.
- **Trust ramp** — discovered tools (plugins, MCP) are treated as `read-only`
  regardless of self-declaration until promoted in
  `.harness/tool-tier-overrides.toml` (promotions >365 days old surface as
  staleness warnings — `policy_warning_sink`).
- **Credential guard** — `.harness/*credentials*` and the tier-override file
  are unreadable/unwritable by agent tools: no credential exfiltration, no
  self-promotion.

## 7. Skills (progressive disclosure)

**Concept.** Knowledge ships as on-demand playbooks, not prompt bloat: a
one-line catalogue entry per skill in the system prompt; the body enters
context only when the `skill` tool loads it.

**Implemented** (`src/dream/skills/`): `SKILL.md` frontmatter parsing
(name/description/when_to_use, `tools_required`, `disable_model_invocation`),
layered discovery (bundled → user home → project, with shadow reporting),
`SkillRegistry` + `render_skill_catalogue`, per-session `SkillContext` on the
dispatcher's `context_metadata`, auto-discovery in `build_harness`
(`skills=False` to disable; explicit `skill_registry` wins).

## 8. Workspace memory (read side)

**Concept.** Durable per-project facts live *outside* the repo under the
dream home, surfaced like skills: catalogue in the prompt, full records on
demand. Writing/curation is deliberately the consuming repo's job.

**Implemented** (`src/dream/memory/`): markdown records with frontmatter,
`FileMemoryStore` over `project_memory_dir(home, working_dir)`,
`render_memory_catalogue`, `MemoryContext` on `context_metadata`, the
`memory_search`/`memory_get` tools, `memory=False` opt-out.

## 9. MCP, plugins, hooks — the extension surfaces

**MCP** (`src/dream/mcp/`): the per-repo allowlist
(`.harness/mcp-allowlist.toml`) is the *admission authority* — name, endpoint
(`stdio://` command, `http`, `ws`), optional tool pins. `setup_mcp_session`
(in `dream.mcp`, REPL re-exports): read → admit → connect (`McpClientManager`
over the official `mcp` SDK) → register adapters; blocking findings degrade
to "no MCP" rather than aborting. Credentials in
`.harness/mcp-credentials.toml` (guarded).

**Plugins** (`src/dream/plugins/`): repo-local `plugins/<name>/` with
`manifest.toml` + entry exposing `get_plugin(manifest) -> Plugin` (a bundle of
tools/hooks/skills/providers). Opt-in via `.harness/plugins-enabled.toml`,
capability-gated against the tier (keyed by the `SandboxTier` enum itself),
5s init timeout, version-pin mismatch warns. One plugin's failure never
aborts the rest; a tool-name collision skips that plugin whole.

**Hooks** (`src/dream/hooks/`, fire points in `engine/`): observer-only
lifecycle taps — `HookSpec(events=...)` over `SESSION_START,
USER_PROMPT_SUBMIT, PRE_TOOL_USE, POST_TOOL_USE, PRE_COMPACT, POST_COMPACT,
SUBAGENT_STOP, STOP, NOTIFICATION`. `HookExecutor` is deadline-bounded and
crash-isolated (a raising hook cannot break a turn); PRE/POST strictly
bracket each dispatch, preserving the tool-call atom. Hooks **never veto** —
blocking belongs to the permission gate. Built lazily per session, so
`register_hook` after `build_harness` still takes effect.

## 10. Background tasks, cron, wake

**Concept.** Long commands shouldn't block turns, and always-on agents need
scheduled consciousness without an always-burning model.

**Implemented** (`src/dream/tasks/`, `src/dream/wake/`, `services/cron.py`):
`BackgroundTaskManager` (detached processes, durable logs, archives) behind
the `task_*` tools; cron manifests (`.harness/cron/*.toml`) seeded into a
registry (`.dream/cron/registry.json`) with per-job misfire policy
(`fire_once | skip`); the wake scheduler drives single-decision heartbeat
turns advertising only `HeartbeatTool` — `wake_model=` runs them on a cheap
model, and an empty checklist skips the model call entirely (zero-cost wake).

## 11. Runtime (always-on)

**Concept.** The harness answers calls; the *runtime* keeps an agent alive —
boot gates, wake loop, cron ticks, command intake, liveness.

**Implemented** (`src/dream/runtime/`, `daemon`, `ctl`, `channels`):
`Runtime(harness).run_forever()`, `python -m dream.daemon`, command inbox
drop-dir + `python -m dream.ctl`, boot gates (malformed skills/config block
boot), lease watchdog, job wall-clock budgets + retry policy, and
runtime-supervised swarm workers (remote bridge intentionally refuses).

## 12. Observability

**Concept.** Two layers: *macro* run events for whoever drives the loop, and
*durable traces* for forensics — plus the agent's ability to query its own
trace (§5a `query_logs`/`query_metrics`).

**Implemented.** Observer protocol (`on_event(dict)`) + `StdioObserver`
(`src/dream/runner/_observer.py`) with event kinds spanning
`task.started/completed`, `planner.*`, `sprint.*` (incl. `sprint.escalated`),
contract events, generator/evaluator session open/close with streamed text
and tool calls, and `head.retry`; OTel-shaped JSONL trace per session
(`JsonlTracer`/`TraceWriter`, under `DREAM_HOME`); runtime events JSONL +
`dream.tail_events`; `SessionCost` accounting (gateway-dependent today).

## 13. Sandbox execution

**Concept.** *Where* approved commands run, separate from *whether* they may
run (§6). One execution mechanism so a backend swap can't reopen confinement
holes.

**Implemented** (`src/dream/sandbox/`): `SandboxAdapter` protocol +
`SubprocessSandbox` backend (`select_backend("subprocess")`), stamped into
every session's `context_metadata` (`SANDBOX_CONTEXT_KEY`); the `bash` tool
resolves and confines `cwd` *before* consulting the adapter (absolute/`..`
escapes refused), maps timeouts and stdout/stderr back through the
three-part error contract. Docker is a deliberate refusing seam.

## 14. Glossary of context keys

Per-session `context_metadata` (dispatcher → every tool's `ctx.metadata`):

| Key | Carries |
|---|---|
| `TASK_CONTEXT_KEY` | `TaskSessionContext` — task manager, cron registry path, plans root for the `task_*`/`cron_*`/`plan_show` tools. |
| `SKILL_CONTEXT_KEY` | `SkillContext` — registry + available-tool set for the `skill` tool. |
| `MEMORY_CONTEXT_KEY` | `MemoryContext` — store for `memory_search`/`memory_get`. |
| `SANDBOX_CONTEXT_KEY` | The `SandboxAdapter` the `bash` tool executes through. |

---

*Verification culture: every surface above is unit-tested (2,676+ tests,
ruff + mypy strict gates) and was proven live against a real model with
unforgeable oracles — dispatch traces, hook-written files, adapter spy logs —
in `scripts/e2e_*.py`, including one capstone run exercising all surfaces in
a single `run_task` (`scripts/e2e_full_surface.py`).*
