# 00 — Harness Architecture & Build Order

**One-liner:** A buildable technical specification for an autonomous, long-running,
multi-agent harness modelled on OpenHarness — the scaffold that turns a frozen base model
into a system that does real software work over days and improves itself over months. This
document is the index and the architecture; specs 01–13 are the buildable components.

**Sources:** [openharness] module decomposition (reference implementation) · `docs/specs/new-specs/01–14` (the conceptual harness specs this set elevates to implementation grade) · `plans/dream-harness-synthesis.md` (the four-clocks framing) · `self_improving.md` Part IV (AHE: "the harness is the dataset")

---

## What this set is, and what it is not

This is the engineering blueprint for a harness in the sense `self_improving.md` Part IV
fixes the word: **the scaffold around a fixed model** — system rules, tools, skills,
middleware, sub-agents, memory, and the loop that drives them. You usually cannot retrain
the frontier model; the harness is the artifact you *do* control, and the one that
compounds. OpenHarness (HKUDS/OpenHarness, Python) is the reference implementation we mirror
for module layout, data structures, and naming, because it is the only inspiration source
that shipped the *whole* loop as running code.

**In scope:** the agent runtime (turn loop, providers, context), the action surface (tools,
skills, MCP), the work layer (task engine, claim/recovery, autopilot pipeline), the
coordination layer (swarm, bridge), and the slow-clock layer (memory/self-evolution,
verification/observability, sandbox/governance/hooks/plugins).

**Out of scope:** the *company/product* layer that sits on top of the harness (employee
roles, dashboards, onboarding — those live in `plans/specs/` and `plans/agent-redesign/`);
the specific channels (Slack/Telegram/etc.) which are pluggable adapters, not harness core;
multi-host distribution (single-host v1 throughout, flagged where it matters).

These specs are **architectural prose**, not code: component responsibilities, data shapes,
behaviours, decisions, acceptance criteria. They name OpenHarness's real classes and
functions so they can be read alongside the reference, but they do not reproduce its source.

## The organising idea: four clocks

Read the synthesis (`plans/dream-harness-synthesis.md`) for the full argument; the
one-paragraph version is that a long-running harness is really four nested loops running at
different speeds, and most design confusion comes from answering a fast-clock question with
a slow-clock mechanism or vice versa:

- **Turn clock (seconds–minutes):** one agent loop — read → plan → act → verify → record.
  Owned by the **engine** (spec 03) over **providers** (02) and **context** (04), acting
  through **tools/skills/MCP** (05, 06).
- **Session clock (hours, the first crash):** a process dies and another picks up its work.
  Owned by the **task engine** (07) and **claim/recovery/liveness** (08).
- **Sprint clock (days, the ledger):** a unit of work with a life of its own — intake →
  verify → PR → CI → repair → merge. Owned by the **autopilot pipeline** (09) and
  **orchestration/swarm** (10).
- **Month clock (memory & evolution):** the harness gets *better* without a human in the
  loop and without drowning in its own history. Owned by **memory/self-evolution** (11),
  **verification/observability** (12), and **sandbox/governance** (13).

## Component decomposition (mirrors OpenHarness packages)

| Spec | Component | OpenHarness package(s) | Clock |
|---|---|---|---|
| [01](01-repo-and-filesystem.md) | Repo as system of record, worktree/sidecar isolation | `swarm/worktree`, `config/paths` | all |
| [02](02-config-and-providers.md) | Config schema, provider abstraction, model failover | `config/`, `api/` | turn |
| [03](03-engine-turn-loop.md) | Engine: turn loop, streaming, cost | `engine/` | turn |
| [04](04-context-engineering.md) | Context window, compaction, session memory | `engine` compaction, `services/session_memory` | turn |
| [05](05-tools-and-action-space.md) | Tools & action space | `tools/` | turn |
| [06](06-skills-and-mcp.md) | Skills (progressive disclosure) & MCP client | `skills/`, `mcp/` | turn/month |
| [07](07-task-engine-and-cron.md) | Background task engine & cron | `tasks/`, `services/cron_scheduler` | session |
| [08](08-claim-recovery-liveness.md) | Claim, recovery & liveness | `swarm/lockfile`, `utils/file_lock` | session |
| [09](09-autopilot-pipeline.md) | Autonomous repo-work pipeline | `autopilot/` | sprint |
| [10](10-orchestration-and-swarm.md) | Orchestration, swarm & bridge | `coordinator/`, `swarm/`, `bridge/` | sprint |
| [11](11-memory-and-self-evolution.md) | Memory & self-evolution | `memory/`, `services/autodream`, `services/memory_extract` | month |
| [12](12-verification-and-observability.md) | Verification, evals & observability | (cross-cutting; traces) | month |
| [13](13-sandbox-governance-hooks-plugins.md) | Sandbox, security, governance, hooks, plugins | `permissions/`, `sandbox/`, `hooks/`, `plugins/` | month |

## Cross-cutting invariants (every spec must honour)

These four hold at every clock; a component that violates one is wrong regardless of how
well it does its own job.

1. **The tool-call atom.** A tool call and its result are an indivisible unit. Never
   compact between them, never inject between them, never drain a queue between them, never
   checkpoint with a `tool_use` block whose `tool_result` is missing. This single rule kills
   an entire genus of provider-400 bugs and corrupt transcripts; it recurs in the turn FSM
   (03), compaction (04), queue drain (10), and recovery (08). `sanitize_conversation_messages`
   in `engine/query` exists precisely to enforce it on restored history.
2. **The repo is the system of record.** Durable state is committed files; anything that
   lives only in a process's memory or an external DB makes the harness uninspectable and
   unreproducible. Git buys diffs, blame, revert, and branches for free (spec 01). The one
   sanctioned exception is the coordination store (08) — *now*-state, not *of-record* state.
3. **Orthogonal components, attributable failures.** Each component is a separate
   git-tracked file/dir so a capability complaint resolves to *one layer*. "The agent can't
   paginate" becomes "the tool *description* (06) omits a param the *implementation* (05)
   supports" — a single-file, revertible fix. Never mix tool logic into system rules, never
   stuff memory into the system prompt, never let two components own one job. (AHE §34.)
4. **Bounded everything, escalate at the cap.** Every loop has a ceiling and an escalation
   path: injection cycles (03), claim failures (08), repair attempts (09), retrieval depth
   (06). The three invariants carved above the door: *productive work continues, only real
   blockers stop the agent, no infinite loops.*

## Storage layout

Two roots, with a hard split between *of-record* (git, low write rate, auditable) and *now*
(fast store, high write rate, ephemeral):

```
# Per-project, committed (system of record — spec 01)
<repo>/
  docs/                         # the product: specs, wiki, design docs
  docs/exec-plans/active/       # task ledger (07) + autopilot cards (09)
  .harness/
    worktrees/{task-id}/        # isolated per-task git worktrees (01, 10)  [git-ignored]
    sidecars/{task-id}/         # ephemeral working memory (04, 11)         [git-ignored]
    coordination/board.sqlite   # claim/liveness CAS store (08)             [git-ignored]
  refs/harness/checkpoints/{task-id}/{n}   # turn checkpoints (01, 03)

# Per-user, global config + cross-project state (mirrors ~/.openharness)
~/.harness/
  settings + provider profiles  # config (02)
  memory/{project}-{sha}/MEMORY.md + *.md   # durable memory (11)
  skills/                       # user skills (06)
  tasks/                        # background task output logs (07)
  teams/<team>/agents/<id>/inbox/   # swarm mailbox (10)
```

## Build order (minimal start, grow as earned)

AHE's strongest operational finding (§38): **a working harness needs only two components —
system rules + one tool (description + implementation) — and evolving *up* from minimal
beats pre-configuring everything.** Every component should exist because a trace proved it
was needed, not speculatively. The build order below respects both that principle and the
dependency graph:

1. **Walking skeleton (turn clock):** 02 config/providers → 03 engine loop → 05 one tool
   (`bash` or `file_read`) → 01 repo/filesystem for durable output. This is a usable
   single-turn agent. Stop here and dogfood before adding anything.
2. **Real turns:** 04 context/compaction (so sessions outlive the context window) → more of
   05's tool catalog → 06 skills + MCP (action surface grows on demand).
3. **Work that survives crashes (session clock):** 07 task engine → 08 claim/recovery
   (start with the *status-guard floor*; add the two-lock lease only when you actually run
   multiple runners).
4. **Autonomous delivery (sprint clock):** 09 autopilot pipeline → 10 orchestration/swarm
   (only when a real bottleneck demands parallelism — not before).
5. **Self-improvement (month clock):** 12 verification/observability (you cannot improve on
   a scalar — traces first) → 11 memory/self-evolution → 13 sandbox/governance/hooks/plugins
   as the safety envelope hardens.

The ordering is also a risk gradient: 13's governance gate is *last to build* but *first in
authority* — nothing in the self-improvement loop may edit the constitution without it.

## How to read a spec in this set

Every spec follows the same shape (the convention established in `docs/specs/new-specs/`):
**One-liner · Sources · Why this matters · Scope (In/Out) · Key decisions · Artefact shapes
· Behaviours · Acceptance criteria (MUST/SHOULD) · Acceptance scenarios (Gherkin) · Tests ·
Edge cases · Open questions · Out of scope.** Cross-references use `#NN` for specs in this
set and `new-spec NN` for the conceptual originals under `docs/specs/new-specs/`.

## Relationship to the existing material

This set does not replace `docs/specs/new-specs/` — it *implements* it. Each conceptual spec
there has a home here: 01–02 fold into harness-01/02, new-spec 03→03, 04→04, 05→05+06,
06→10, 07→12, 08→13, 09→07, 10→11, 11→02, 12→13, 13→08, 14→09. Where the conceptual spec and
OpenHarness disagree, the spec records both and picks a default (e.g. harness-08 tiers the
claim protocol because OpenHarness proves a status guard suffices single-runner, while
new-spec 13 specified the full lease unconditionally).
