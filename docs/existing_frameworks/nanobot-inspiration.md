# Nanobot — Inspiration Notes for a Long-Running, Self-Evolving Agent Harness

Source: https://github.com/your-fork/nanobot (this repo). Notes captured while exploring with the goal of building a **multi-agent "company" harness** with hierarchy, memory, governance, and skill evolution that can run for months across sprints.

Nanobot is a single-process, single-personality chat bot — much smaller in ambition than what we are building — but it has made surprisingly thoughtful choices in exactly the four areas we care about. Below is what's worth stealing, what's worth reframing, and where the metaphors break down.

---

## 1. Turn / Orchestration Loop

### 1.1 Explicit, named turn states with a transition table

[`nanobot/agent/loop.py`](nanobot/agent/loop.py) defines `TurnState` as an `Enum` (`RESTORE → COMPACT → COMMAND → BUILD → RUN → SAVE → RESPOND → DONE`) plus a literal `_TRANSITIONS: dict[tuple[State, event], State]`. Handlers return an event string; the driver looks up the next state. Every transition produces a `StateTraceEntry(state, started_at, duration_ms, event, error)`.

**Why it's smart**

- A long-running agent will spend most of its lifetime *between* token-generation calls. Making the inter-turn lifecycle a tiny FSM gives you:
  - replayable traces ("this turn failed at SAVE, not RUN")
  - clean places to insert governance hooks (between BUILD and RUN, between RUN and SAVE)
  - the ability to checkpoint and resume mid-turn after a crash
- Returning *events* instead of next-states means handlers don't know the graph — easy to slot in new states (e.g. `APPROVAL`, `BUDGET_CHECK`) without rewriting handlers.

**For us**

- Make the per-agent lifecycle (`PLAN → APPROVE → DELEGATE → EXECUTE → REPORT → REFLECT`) an explicit FSM with a transition table. The "company" wraps a tree of these FSMs.
- Persist `StateTraceEntry` to disk per agent — gives you a free flight-recorder for the post-mortem that any long-running system eventually needs.

### 1.2 Mid-turn injection with a bounded queue

[`loop.py`](nanobot/agent/loop.py) keeps a `_pending_queues: dict[session_key, asyncio.Queue]`. When a new user message arrives while the agent is mid-turn, it's *not* a new turn — it's pushed into the queue and the runner drains it between iterations via an `injection_callback` ([`runner.py`](nanobot/agent/runner.py) `_drain_injections`, `_MAX_INJECTIONS_PER_TURN = 3`, `_MAX_INJECTION_CYCLES = 5`).

**Why it's smart**

Naive "interrupt the turn / cancel the task" patterns lose all the in-flight work. Naive "queue everything as new turns" loses urgency. Bounded injection lets a parent/manager agent steer a working child mid-flight without throwing away the LLM state.

**For us**

This is the only sane primitive for a manager agent that needs to say *"actually, also consider X"* to a worker that's already 12 tool-calls deep into a sprint task. Steal this pattern verbatim, but generalise the source from "user typed something" to "any AgentEvent from the hierarchy."

### 1.3 Per-session lock + global concurrency semaphore

```python
self._session_locks: dict[str, asyncio.Lock] = {}
_max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "3"))
self._concurrency_gate = asyncio.Semaphore(_max) if _max > 0 else None
```

Simple, but the combination matters: serialise per-conversation (consistency) while still letting N conversations run in parallel (throughput). For us this maps directly to *per-agent serialisation + org-wide rate limit*.

### 1.4 Command router as three-tier dispatch

[`command/router.py`](nanobot/command/router.py) defines `priority` (handled *before* the per-session lock, so `/stop` works on a stuck turn), `exact`, `prefix`, then `interceptors`. The priority tier is the interesting bit — there must always be a way to interrupt a misbehaving agent without waiting for it to release its own lock. Build the same escape hatch for governance commands (`pause`, `freeze`, `revoke-tool`).

---

## 2. Memory

This is nanobot's most sophisticated subsystem and the most directly transferable to our project. It splits memory along **two orthogonal axes**: *latency* (hot/cold) and *editor* (LLM vs deterministic).

### 2.1 Four-file memory model

In [`agent/memory.py`](nanobot/agent/memory.py) `MemoryStore`:

| File | Role | Editor | Loaded into prompt |
|---|---|---|---|
| `memory/history.jsonl` | Append-only event log, autoincrement cursor | Deterministic | No (grep'd on demand) |
| `memory/MEMORY.md` | Long-term project facts | **Dream** (LLM) | Yes |
| `SOUL.md` | Agent personality / behavior | **Dream** (LLM) | Yes |
| `USER.md` | Counterparty profile | **Dream** (LLM) | Yes |

The key trick: the **only** writer of the human-readable files is an asynchronous "Dream" job. The agent itself can read those files but is told not to edit them. The system prompt literally says "Managed by Dream. Do NOT edit." in [`skills/memory/SKILL.md`](nanobot/skills/memory/SKILL.md).

**Why it's smart**

In a system that runs for months, the failure mode of "agent edits its own memory" is well-known: drift, sycophancy capture, prompt-injection rewrites of beliefs. Separating *evidence accumulation* (cheap append) from *belief consolidation* (rare, audited LLM pass) is the right factoring. We should adopt it.

### 2.2 Two-phase Dream (consolidation)

[`Dream.run()`](nanobot/agent/memory.py) processes new `history.jsonl` entries in batches with a **cursor that only advances on success**:

- **Phase 1**: plain LLM call with the history batch + current file previews. Output is a structured directive list:
  ```
  [USER] location is Tokyo, not Osaka
  [MEMORY-REMOVE] reason
  [SKILL] kebab-name: one-line description
  ```
- **Phase 2**: a *second* agent run with `read_file` / `edit_file` / `write_file` tools whose write scope is restricted to `skills/`. The Phase 1 directives are the input — the LLM applies them surgically. The Dream cursor only advances when Phase 2 returns `stop_reason == "completed"`. On failure the cursor stays put and the next cron tick retries the same batch.

**Why it's smart**

- **Plan/Act split.** Phase 1 decides *what* should change; Phase 2 does the file surgery. This is the same pattern Anthropic's research mode uses, and it really does cut hallucinated edits.
- **Idempotent retries.** Cursor-on-success means a crash mid-consolidation never loses or duplicates entries. We need this for sprint-long runs.
- **Git auto-commit.** When Phase 2 produces changes, [`GitStore`](nanobot/utils/gitstore.py) commits MEMORY/SOUL/USER with the Phase 1 analysis as the commit body. Free, perfect audit log of "why does the agent now believe X." For us this could be *the* governance artifact — every belief change is a commit; revocation is `git revert`.

### 2.3 Per-line age annotation in MEMORY.md

`Dream._annotate_with_ages` reads `git blame` line ages and appends `← 30d` suffixes to stale lines before showing MEMORY.md to the consolidator. The model can see *which* facts are old without being forced to delete them. The default `_STALE_THRESHOLD_DAYS = 14` is shared with the prompt template via Jinja — no chance of code/prompt drift.

**Inspiration for us**: long-running company memory will get cluttered. An age annotation derived from git is essentially free and gives the consolidator a *time prior* it would otherwise hallucinate.

### 2.4 Token-budget-aware live consolidation

[`Consolidator.maybe_consolidate_by_tokens()`](nanobot/agent/memory.py) is the in-turn complement to Dream. While Dream is the cron-scheduled deep cleaner, the Consolidator runs every turn:

1. Estimate prompt tokens for the current session.
2. If over `budget * consolidation_ratio` (default 0.5), find a **user-turn boundary** (never split a tool-call/result pair!) that would remove enough tokens.
3. Summarise the chunk to `history.jsonl`, advance `session.last_consolidated`, repeat up to 5 rounds.
4. On LLM failure, dump a `[RAW]` block to history rather than losing it. The cursor still advances so we don't loop.

**Smart details worth stealing wholesale**:

- `pick_consolidation_boundary` only cuts at user turns, preserving tool-call alignment.
- Failure has a graceful raw-archive degrade mode — "drop the LLM, keep the data."
- `_MAX_CONSOLIDATION_ROUNDS = 5` prevents pathological consolidation loops.
- Re-archived chunks get a single `[RAW]` breadcrumb instead of duplicate entries.

### 2.5 Auto-compact on idle (TTL)

[`AutoCompact`](nanobot/agent/autocompact.py) is a *third* compression path triggered by session inactivity. The clever bit is `_RECENT_SUFFIX_MESSAGES = 8`: when a session goes idle, archive everything *except* the last 8 messages and stash a summary in `session.metadata["_last_summary"]`. When the user returns, the summary is re-injected as `[Archived Context Summary]`.

This is *exactly* the pattern we need between sprints. End of sprint → archive working set, keep last few exchanges + a structured summary; start of next sprint → rehydrate from summary, agent doesn't need to re-read 14 days of transcripts.

### 2.6 Atomic writes with directory fsync

`MemoryStore._write_entries` does the full belt-and-braces: temp file → `f.flush()` → `os.fsync(fd)` → `os.replace()` → directory fsync (with Windows skip). At sprint-scale, you cannot afford a power loss to corrupt the memory file. Steal the pattern; it's three lines.

---

## 3. Skill Evolution

This is where nanobot is shockingly close to what we want, despite not framing itself that way.

### 3.1 Progressive disclosure (SKILL.md frontmatter as the index)

[`agent/skills.py`](nanobot/agent/skills.py) + [`skills/skill-creator/SKILL.md`](nanobot/skills/skill-creator/SKILL.md):

- A skill is a directory: `SKILL.md` + optional `scripts/`, `references/`, `assets/`.
- The system prompt only ever sees the **frontmatter** (`name` + `description`) of all skills — a one-liner index.
- When a trigger phrase matches, the agent calls `read_file` on the SKILL.md body. Further detail lives in `references/<topic>.md`, loaded only on demand.
- Skills can declare `requires: { bins: [...], env: [...] }` and are filtered out of the index when prerequisites aren't met. Cheap, accurate availability filter.
- Skills marked `always: true` are loaded into every prompt.

**Why this matters for a long-running multi-agent company**

The cost model of "every agent knows everything" is unsurvivable. Progressive disclosure with a description-only index is the obvious solution — and nanobot's three-level system (metadata / SKILL.md / bundled refs) is already battle-tested on real conversations. Adopt the *directory layout* as our skill packaging format. The description-only index doubles as a tool-router for "which agent should handle X."

### 3.2 Skill creation as a Dream side effect

This is the killer feature. Phase 1 of Dream emits `[SKILL] kebab-name: description` directives when *"a specific, repeatable workflow appeared 2+ times in the conversation history"* (see [`templates/agent/dream_phase1.md`](nanobot/templates/agent/dream_phase1.md)). Phase 2 creates `skills/<name>/SKILL.md` using `write_file` restricted to the `skills/` directory.

The Dream agent's tool registry deliberately scopes its `write_file` allow-dir to `workspace/skills/` only — even if the model goes off the rails, it can't write anywhere else. ([`Dream._build_tools`](nanobot/agent/memory.py))

**Inspiration for us**: this is the seed of self-evolution. The pattern generalises directly:

1. Append every agent interaction to an event log (history.jsonl).
2. Periodically run a "reflection" agent that scans the log for *recurring* patterns.
3. Have it write new skills/playbooks to a sandboxed directory.
4. Have a *governance* agent review and promote them.

The constraint "appeared 2+ times" is critical — it prevents one-shot weirdness from becoming permanent doctrine.

### 3.3 Workspace skills override builtin

[`SkillsLoader.list_skills`](nanobot/agent/skills.py) lists `workspace/skills/` first, then merges in builtins from the package, skipping any name collisions. This is the standard "user overrides library" pattern but applied to *skills* — meaning a long-running agent can override its own factory-default behaviour without forking the framework. For our hierarchy, swap "workspace" for "team scope" and "builtin" for "company scope" and you have layered governance for free.

### 3.4 MyTool: bounded self-modification

[`agent/tools/self.py`](nanobot/agent/tools/self.py) lets the agent inspect and *modify* its own runtime state. The interesting bit is what they refused to expose:

- `BLOCKED` frozenset: `bus`, `provider`, `tools`, `_mcp_servers`, security boundaries, etc. — even introspecting is denied.
- `READ_ONLY` frozenset: `exec_config`, `web_config`, `subagents` — you can look, not touch.
- `RESTRICTED` dict with type + range validation: `max_iterations: int [1,100]`, `context_window_tokens: int [4096, 1M]`, `model: str`.
- `_DENIED_ATTRS` blocks all dunders, preventing `__class__.__subclasses__()` style escapes.
- Sensitive-name detection by substring (`api_key`, `secret`, `token`, ...) — denied at any depth in a dot path.
- `allow_set` config flag for read-only mode.
- Every action is `_audit`-logged with session key.

**Inspiration**: self-modification is unavoidable in a self-evolving agent, but it has to be *capability-limited*. The pattern `BLOCKED / READ_ONLY / RESTRICTED-with-validators / audited` is the right shape. Don't roll your own — copy this taxonomy.

The accompanying [`skills/my/SKILL.md`](nanobot/skills/my/SKILL.md) is also worth studying: it teaches the agent *when not to* introspect ("Don't check every turn. Costs a tool call."), which is exactly the kind of behavioural shaping we need for cost-bounded long runs.

---

## 4. Hierarchy / Sub-agents / Governance

### 4.1 SubagentManager: fire-and-forget background tasks

[`agent/subagent.py`](nanobot/agent/subagent.py) spawns subagents via `asyncio.create_task`, tracks them in `_running_tasks: dict[task_id, Task]` and `_task_statuses: dict[task_id, SubagentStatus]`. Each subagent gets:

- An isolated `ToolRegistry` built via `ToolLoader().load(scope="subagent")` — so subagents don't accidentally inherit, say, the `spawn` tool itself.
- An isolated `FileStates` cache (so read-before-write tracking doesn't leak between parent and child).
- A `_SubagentHook` that streams iteration count, tool events, usage, and stop reason into the shared `SubagentStatus` record so the parent can poll progress.
- A concurrency cap (`max_concurrent_subagents`) enforced in [`SpawnTool.execute`](nanobot/agent/tools/spawn.py).

**Why it's smart for us**

- The `SubagentStatus` dataclass is exactly what a "manager agent's view of a worker" should look like. Phase, iteration, tool history, usage, error, stop reason — all observable, none of it requiring the worker to pause.
- Tool-scope filtering at spawn time is the cleanest implementation of capability handoff I've seen in a small codebase. We should generalise this into a *capability grant* that flows down the hierarchy.

### 4.2 The hook system as a governance bus

[`agent/hook.py`](nanobot/agent/hook.py) defines `AgentHook` with `before_iteration`, `before_execute_tools`, `after_iteration`, `finalize_content`, plus streaming hooks. `CompositeHook` fans out to a list of hooks **with per-hook exception isolation** so a faulty governance plugin can't crash the loop.

This is the seam to attach:

- **Budget enforcement** — `before_iteration`: refuse if remaining tokens < threshold.
- **Policy checks** — `before_execute_tools`: veto specific tool calls based on caller's clearance.
- **Audit logging** — `after_iteration`: emit to a separate governance log.
- **Output filtering** — `finalize_content`: redact secrets before they leave the loop.

The composite-with-isolation pattern is the right default. Adopt it.

### 4.3 Hierarchical session keys

Nanobot uses `f"{channel}:{chat_id}"` as session keys throughout. Trivial, but it has a property we want: keys are structured strings, not opaque UUIDs. A logger or a dashboard can `startswith()` to filter all sessions for one user / one channel / one tenant.

For our company we want this nested further — `org/team/agent/sprint` — so that any subsystem can scope a query by prefix without a join. Cron, locks, queues, and metrics all key on the same string.

### 4.4 Network sandbox + bwrap

[`security/network.py`](nanobot/security/network.py) blocks SSRF to all the usual private ranges (including 169.254.0.0/16 for cloud metadata, which is the one people forget) and resolves hostnames *before* trusting them.

[`agent/tools/sandbox.py`](nanobot/agent/tools/sandbox.py) wraps shell commands in `bwrap` with the parent of the workspace masked behind a tmpfs and only read-only `/usr`, `/etc/ssl/certs`, etc. Even the media dir is read-only.

For a multi-agent company where untrusted output flows everywhere, both are mandatory primitives. The exact ranges are easy to copy.

---

## 5. Long-Running Concerns (Days/Weeks/Months)

### 5.1 Heartbeat: scheduled self-wake

[`heartbeat/service.py`](nanobot/heartbeat/service.py) runs every 30 min by default. **Phase 1** is the clever part: it doesn't dump `HEARTBEAT.md` straight into the agent — it asks the LLM to call a *virtual tool* `heartbeat(action: "skip"|"run", tasks: str)` and only triggers a full agent run when the tool returns `run`. This avoids both free-text parsing *and* the "agent always finds something to do" failure mode of unstructured wake-ups.

**Inspiration**: every long-running agent needs a way to wake itself, but every wake costs tokens and risks drift. Forcing the decision through a *structured* tool call with an explicit `skip` option is the right pattern. We should have one for sprint-boundary check-ins, daily standups, weekly retros — each with its own `<thing>.md` checklist and a virtual decision tool.

### 5.2 Cron with monotonic file-locked store

[`cron/service.py`](nanobot/cron/service.py) keeps jobs in a JSON file guarded by `FileLock` and supports `at`, `every`, and full cron expressions with timezone. Schedules are validated on add (`_validate_schedule_for_add`) — TZ on a non-cron schedule is a hard error. Run history is bounded (`_MAX_RUN_HISTORY = 20`). Nothing flashy; just durable, correct, and small. Steal it before writing your own.

### 5.3 Versioned, git-tracked memory

The single best feature for a months-long agent is that `memory/MEMORY.md`, `SOUL.md`, `USER.md`, and `memory/.dream_cursor` are all in a real git repo, initialised on first run by [`GitStore.init`](nanobot/utils/gitstore.py), with auto-commits keyed to Dream's activity. You get:

- `git log` = full belief-evolution history.
- `git blame` = "when did the agent start believing this?" — and this is what feeds back into 2.3's staleness annotation.
- `git revert` = clean rollback of a bad consolidation.
- Diffable PR review for governance ("the agent now thinks X; approve?").

For a company-scale system, this is the foundation of trust. Use the same pattern per-agent: every agent's beliefs live in a git repo, governance is `git review`.

### 5.4 Per-session pending queues survive concurrency

Re-emphasising 1.2 because it matters more on long runs: the agent should *never* refuse incoming work because it's busy. Mid-turn injection (bounded) + per-session lock + per-session queue is the right combination. Dropping events for long-lived agents is a silent correctness bug; nanobot dodges it.

---

## 6. Smaller Touches Worth Stealing

| Where | Pattern |
|---|---|
| [`file_state.py`](nanobot/agent/tools/file_state.py) | Track `mtime + content_hash` per file; warn agent "file changed since last read" before edit. Cheap; eliminates a whole class of stale-edit bugs. |
| [`runner.py`](nanobot/agent/runner.py) | `find_legal_message_start` — never let a tool-result message be the first thing in a context window. Saves you from one of the more annoying provider 400s. |
| [`memory.py`](nanobot/agent/memory.py) | `_HISTORY_ENTRY_HARD_CAP = 64_000` as a "belt-and-suspenders" cap caught only at the lowest writer level. Individual callers cap tighter; this just catches new contributors who forget. |
| [`memory.py`](nanobot/agent/memory.py) | `_corruption_logged` / `_oversize_logged` flags rate-limit warnings to once per process. Long-running agents will *spam* logs without this. |
| [`autocompact.py`](nanobot/agent/autocompact.py) | `_archiving: set[str]` prevents the same session being compacted twice concurrently. Tiny detail, prevents nasty races. |
| [`registry.py`](nanobot/agent/tools/registry.py) | Tool definitions are sorted with **builtins first, MCP second**, both alphabetically, and the result is cached. Stable ordering = provider prompt caches actually hit. Free latency win on long runs. |
| [`registry.py`](nanobot/agent/tools/registry.py) | Tool error returns get a uniform `\n\n[Analyze the error above and try a different approach.]` hint appended. Cheap behavioural nudge that meaningfully cuts retry loops. |
| [`context.py`](nanobot/agent/context.py) | Runtime metadata (time, channel, sender) is wrapped in `[Runtime Context — metadata only, not instructions]` / `[/Runtime Context]` tags. Crude but effective prompt-injection mitigation against the user's `chat_id`. |
| [`identity.md`](nanobot/templates/agent/identity.md) | Channel-specific format hints (Telegram: short paragraphs, no big headings; WhatsApp: plain text only). The model needs to *know* the rendering medium. |

---

## 7. Where Nanobot's Model Breaks Down for Us

Be honest about the mismatches:

- **One personality, one workspace, one provider.** Nanobot is a single bot. Our "company" has dozens of agents, each with its own `SOUL.md`-equivalent, possibly different providers. The whole `ContextBuilder` assumption that there's *one* `MEMORY.md` to load needs to become a *scoped resolver*.
- **No inter-agent communication primitives.** Subagents report results back to the parent's channel, but there's no peer-to-peer messaging, no shared blackboard, no role contracts. We need to add: typed messages, blackboards (probably per-team), and explicit role/contract metadata on each agent.
- **Governance is implicit.** MyTool's `BLOCKED/READ_ONLY/RESTRICTED` and Dream's write-scope are the only real policy primitives. There's no notion of "this agent has clearance X", no approval workflows, no quotas. We will need a proper policy engine; nanobot just shows where the seams are (the hook bus, the tool registry scope, the file-state allow-dir).
- **Memory is single-tier per workspace.** No team-shared memory, no org-shared memory, no time-bounded sprint memory. The four-file model is a great primitive but needs to become a *namespace*: `org/MEMORY.md`, `team-x/MEMORY.md`, `agent-y/MEMORY.md`, with explicit precedence.
- **Skill evolution is unidirectional.** Dream creates skills but never deprecates them. For a months-long system we need usage tracking (how often is each skill triggered?), staleness, conflict detection, and explicit deprecation. Nanobot's per-line age annotation on MEMORY.md is the template — apply the same to skills.
- **No formal evaluation loop.** Nothing here grades the agent's outputs. For a multi-month run we need (a) golden datasets per skill, (b) periodic evals, (c) regression detection. Plug this into the hook bus after `RUN`.

---

## 8. TL;DR — What To Lift Directly

Ranked by ROI for our use case:

1. **Two-phase Dream consolidation (plan → act-with-tools) with cursor-on-success and git-tracked memory files.** This is the heart of safe self-evolution. Adopt as-is, namespace per-agent.
2. **Append-only `history.jsonl` + LLM consolidator → human-readable `MEMORY.md`.** Separates evidence from belief. Cheap, robust, auditable.
3. **Progressive-disclosure skill packaging** (`SKILL.md` frontmatter as index, body on-trigger, refs on-demand, requires-filtering, workspace-overrides-builtin). The right abstraction for capability libraries that span dozens of agents.
4. **MyTool's BLOCKED/READ_ONLY/RESTRICTED/audited taxonomy** for any "agent modifies its own state" surface.
5. **Mid-turn injection with bounded queue** for steerable, non-cancelling parent→child interaction.
6. **Explicit turn FSM with transition table + state trace.** Replayable, hookable, debuggable.
7. **Auto-compact-on-idle with "summary + last-N messages" survival pattern.** This *is* the sprint-boundary archive primitive.
8. **Heartbeat with virtual-tool decision** (`skip`/`run`). Right way to do periodic self-wakes.
9. **CompositeHook with per-hook exception isolation.** Default governance bus.
10. **Workspace-scoped tool registry + bwrap sandbox + SSRF list.** Mandatory baseline; the exact ranges and bwrap args are copy-paste-ready.

The repo is small enough to read end-to-end in a couple of hours and the design choices are remarkably consistent. Even where we can't reuse code, the *taxonomy* (live-consolidate vs idle-compact vs cron-dream; index vs body vs ref; BLOCKED vs READ_ONLY vs RESTRICTED) is worth lifting verbatim into our design vocabulary.
