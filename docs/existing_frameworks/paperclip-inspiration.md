# Paperclip — Inspiration for a Self-Evolving, Long-Running Agent Harness

Date: 2026-05-15
Audience: Architects of a long-running, self-evolving agent company harness
Source: deep read of the open-source `paperclip` repo (`server/`, `packages/`, `doc/`, `skills/`)

This is not a code-port plan. It is a curated set of design ideas — some clever, some refreshingly simple — that we should steal, adapt, or at least argue with as we build a harness whose agents must run for **days, weeks, sprints**, not minutes.

The single most important framing in the entire repo is this:

> Paperclip is a **control plane**, not an execution plane. Agents run wherever they run and **phone home**.

That separation is what makes everything else possible: long horizons, recovery, governance, memory, evolution. We should build with the same separation from day one.

---

## TL;DR — the ideas worth stealing

1. **Heartbeats over daemons.** Long-running ≠ a process that runs forever. It's a sequence of bounded "wake → orient → do one useful thing → exit" cycles, with all continuity living in the control plane.
2. **Single-assignee + atomic checkout.** A whole class of multi-agent bugs disappears when only one agent owns a piece of work at a time, enforced by a DB-level lock.
3. **A typed liveness contract.** Every non-terminal piece of work must answer "what moves this forward next?" with one of a small, named set of action paths. No silent dead state.
4. **Recovery as a first-class typed object**, not "retry with backoff." Recovery actions name owner, cause, evidence, next action, wake/monitor policy, and resolution outcome.
5. **Three invariants stated as a contract**, not an if/else: productive work continues, only real blockers stop work, no infinite loops. Every new rule must explicitly hold all three.
6. **Goal-chained tasks.** Every task must trace through `parentId` / `goalId` to the company goal. This is what keeps long-horizon agents aligned.
7. **Issues + comments as the universal communication substrate.** Not chat. Not RPC. Coordination, decisions, evidence, handoffs, all anchored to a durable work object.
8. **Skills as portable, governed capability packages.** Versioned, scoped, importable, declarable by plugins.
9. **Plugins/adapters as the only extension point.** Core has zero hardcoded provider knowledge. Evolution happens at the edges.
10. **Activity log → live events → plugin events** as a single funnel. One audit trail powers UI realtime, plugin notifications, and forensics.

---

## 1. Orchestration

### 1.1 Heartbeats, not loops

The deepest architectural choice in Paperclip: an "always-on" agent is modeled as a **sequence of short heartbeat runs**, each triggered by a wake source.

A wake comes from one of four sources only:
- `timer` — a scheduled tick
- `assignment` — work was assigned/checked out
- `on_demand` — a human or peer pinged the agent
- `automation` — system-triggered (recovery, monitor due, blockers cleared, etc.)

If the agent is already running, **new wakes are coalesced** into the live run, not enqueued as duplicates.

**Why this matters for us.** A "long-running agent" is the wrong abstraction. Processes die, contexts go stale, models change, prompts evolve. What we actually want is a **durable identity that wakes up periodically with fresh context**. Heartbeats give us that for free, plus they are naturally bounded and naturally observable.

See: [docs/agents-runtime.md](../docs/agents-runtime.md), [server/src/services/heartbeat.ts](../server/src/services/heartbeat.ts).

### 1.2 Wake context as a first-class payload

Every wake carries a typed payload (`PAPERCLIP_WAKE_PAYLOAD_JSON` in the agent's env). For comment wakes it includes the compact issue summary plus the **ordered batch of new comments since the last wake**. The agent skill explicitly says: *use the inline batch first; only call the API if a flag says you must*.

This is a small thing that buys a lot: it means the agent doesn't need to re-read the world every wake to know "what happened while I was asleep." The control plane already did the diff.

**Steal:** Always pre-package the delta the agent needs into the wake payload. Don't make the agent re-derive it from logs.

### 1.3 Single-assignee + atomic checkout

```
- single assignee only
- in_progress requires assignee
- checkout is required to move an issue into agent-owned in_progress
- checkoutRunId answers who currently owns execution rights
- executionRunId answers which run is actually live right now
```

Two locks, not one. `checkoutRunId` is the *ownership* lock, `executionRunId` is the *liveness* lock. They can drift (e.g. crash leaves a stale execution lock with a still-valid checkout) and recovery treats them differently.

A contended checkout returns **`409 Conflict` and the skill explicitly says "never retry a 409"**. That single rule prevents the most common multi-agent race in this kind of system.

**Steal:** Treat ownership and execution as two separate, explicit DB-backed locks. Make 409 a teaching signal in the agent prompt.

### 1.4 Coalescing and start-locks

[`agent-start-lock.ts`](../server/src/services/agent-start-lock.ts) is a 50-line gem: an in-process per-agent lock with a stale-after-30s timeout, used so that two near-simultaneous starts of the same agent don't double-spawn. The pattern: "wait up to N for the previous start, then proceed anyway and log a warning."

**Steal:** Per-agent in-process serialization is cheap and prevents a lot of weird races. Don't over-engineer it with distributed locks until you need to.

### 1.5 Cron + routines as a separate primitive

Routines (recurring scheduled tasks) are a separate concept from agent timers. They have their own [cron parser](../server/src/services/cron.ts), their own revisions, their own runs, and they trigger heartbeats by **enqueueing an issue** rather than directly invoking the agent. The agent finds the work in its inbox like any other task.

**Steal:** Cron should produce *work items*, not direct agent invocations. Then everything (priority, dedupe, governance, audit) just works because the control plane already handles work items.

---

## 2. Validation / Liveness — the most original part of the system

This is the area where Paperclip has the most non-obvious, hard-won design. If we read nothing else, read this section.

### 2.1 The liveness contract

From [`doc/execution-semantics.md`](../doc/execution-semantics.md):

> An issue is healthy when the product can answer **"what moves this forward next?"** without requiring a human to reconstruct intent from the whole thread.

For every agent-owned, non-terminal issue, **at least one** of these "action-path primitives" must be true:

- an active run linked to the issue
- a queued wake or continuation
- a typed execution-policy participant
- a pending issue-thread interaction or linked approval awaiting a specific responder
- a one-shot issue monitor (`executionPolicy.monitor.nextCheckAt`) that will wake the assignee
- a human owner (`assigneeUserId`)
- a first-class blocker chain whose unresolved leaf is itself healthy
- an open explicit recovery action

Any other state is **stalled** and surfaced.

**Why this matters enormously for long-running harnesses.** When agents run for weeks, the failure mode isn't crashes — it's **silent dead states**: tasks that look fine but have no path forward, comments that look like progress but aren't, "in review" with no reviewer. Without an explicit liveness contract, your dashboard slowly fills with zombies that nobody notices. With one, the system can *prove* every piece of work is alive.

### 2.2 Three invariants, stated as a load-bearing contract

From the [`diagnose-why-work-stopped` skill](../skills/diagnose-why-work-stopped/SKILL.md):

> Every diagnosis and every proposed rule must hold these three invariants together:
> 1. **Productive work continues.** Agents with a clear next action must keep working without needing the user to wake them.
> 2. **Only real blockers stop work.** Pseudo-stops must be detected and routed, not left silent.
> 3. **No infinite loops.** Stranded-work recovery and continuation loops must be bounded and distinguishable from genuinely productive continuation.

Notice: **all three together**. Each one is easy alone; the engineering challenge is that they pull in opposite directions. A new rule that improves one invariant while violating another is rejected.

**Steal verbatim.** Write these three invariants on the wall. Every recovery rule, every retry policy, every status transition we ever introduce should explicitly state how it preserves all three.

### 2.3 Status semantics that mean what they say

Most task systems treat statuses as UI labels. Paperclip treats them as **execution contracts**:

| Status | Contract |
|---|---|
| `backlog` | parked, no execution expectation |
| `todo` | actionable, may need a wake path |
| `in_progress` | strict for agents — must have live execution path |
| `in_review` | execution paused; next move belongs to a named participant |
| `blocked` | must have an explicit waiting path (named blocker, owner, monitor) |
| `done` / `cancelled` | terminal |

A successful run that leaves the issue in `in_progress` with no next-step path is **invalid**, not "fine." This is enforced at the post-run disposition check.

**Steal:** Don't let agents leave work in ambiguous states. Define each status as a contract and validate every transition.

### 2.4 Parent/child vs blockers — separated on purpose

Two relationships, two jobs:

- `parentId` is **structural** (work breakdown, rollup context).
- `blockedByIssueIds` is **dependency** (waits, auto-resume).

Don't conflate them. The system gives you `issue_blockers_resolved` and `issue_children_completed` as separate wake reasons, with different semantics: the first means "go!", the second means "decide what to do now that your subtree finished."

**Steal:** Resist the temptation to overload one relationship. Wakes that fire on the wrong relationship are a giant source of loops.

### 2.5 Bounded continuation, not unbounded retries

`run_liveness_continuation` is **bounded by attempt count** with a default ([`recovery/run-liveness-continuations.ts`](../server/src/services/recovery/run-liveness-continuations.ts)). When the bound is hit, the system stops auto-continuing and either opens a recovery action or routes to a human. This is the load-bearing piece that prevents infinite loops while still allowing genuinely productive continuation.

**Steal:** Every "self-healing" mechanism must have a hard, observable bound.

### 2.6 Productivity review

[`productivity-review.ts`](../server/src/services/productivity-review.ts) is a watchdog that flags work that's *moving but not productive*: high run churn with no comments, long active duration with no progress, no-comment streaks across many runs. Threshold defaults: 10 runs without a comment, 6 hours active, 10 runs/hour churn, 30 runs/6h.

It produces evidence (recent runs, comments, costs, usage), creates a review issue, and snoozes itself for 6h after resolution to avoid noise.

**Why this is critical for long-horizon agents.** A model can happily burn $100/day looking busy without producing anything. You need a *separate* signal beyond "is the process alive" — a "is this actually delivering" signal.

### 2.7 Silent active-run watchdog

A run can be `running` (process is up) but be silent (no output for hours). Paperclip classifies this as `ok | suspicious | critical | snoozed`, with thresholds (1h suspicious, 4h critical), and creates **at most one** open watchdog recovery per run. Operators can `snooze`, `continue` (re-arm 30 min), or mark `dismissed_false_positive`.

**Steal:** "Process alive" ≠ "agent alive." Distinguish them.

---

## 3. Recovery as a typed first-class object

This is the design pattern I think we should adopt most aggressively. Recovery in Paperclip is not "retry with backoff" — it's a typed object with mandatory fields:

```
- source issue and company
- recovery kind and idempotency fingerprint
- owner (+ previous/return owner for handoffs)
- cause, bounded evidence, next action
- wake/monitor/timeout/retry/escalation policy
- resolution outcome (restored | delegated | false_positive | blocked | escalated | cancelled)
```

See [`issue-recovery-actions.ts`](../server/src/services/issue-recovery-actions.ts) and [`recovery/service.ts`](../server/src/services/recovery/service.ts).

Three escalation tiers, used in order of confidence:

1. **Auto-recover** — control plane lost continuity but ownership is clear. Requeue one wake. (Source-scoped, preserves the original owner.)
2. **Explicit recovery action** — system can identify the problem but can't safely fix it. Default is *source-scoped* (rendered on the source issue); only promote to a separate "recovery issue" if the repair is genuinely independent work.
3. **Human escalation** — board judgment required.

A unique constraint per `(companyId, sourceIssueId, status=active)` prevents recovery storms. The service uses a per-issue in-process queue (`runExclusiveUpsert`) plus DB unique constraints — defense in depth.

**Steal:** Recovery deserves a real type, real fields, real fingerprints, real bounds. "Just retry" is an anti-pattern in long-horizon systems because the next failure is rarely the same as the last.

---

## 4. Memory

[`doc/memory-landscape.md`](../doc/memory-landscape.md) is one of the more honest surveys I've read on agent memory. The conclusions are valuable in themselves:

### 4.1 Don't be a memory engine — be a memory binding layer

Paperclip explicitly refuses to standardize one memory backend. Instead it specifies:

- **Paperclip owns:** binding a provider to a company / agent / project, mapping its entities into provider scopes, **provenance back to issues/comments/runs**, cost and latency reporting, browse/inspect surfaces, governance on destructive ops.
- **Provider owns:** extraction heuristics, embedding/indexing strategy, ranking, profile synthesis, contradiction resolution, storage details.

The portable core is just six operations: `ingest`, `query`, `browse`, `getByHandle`, `forget`, `usageReport`. Everything else (profile synthesis, async ingestion, multimodal, skill memory, graph browsing) is an **optional capability flag**.

**Steal:** Define the smallest possible memory contract. Make richness a capability, not a requirement. This is the only way to support both a 100-line markdown index *and* a hosted graph database without flattening either.

### 4.2 Provenance is the load-bearing field

The single most important field across every memory system surveyed (mem0, supermemory, Memori, MemOS, EverMemOS, OpenViking, memsearch, Bedrock AgentCore) is **provenance** — what control-plane entity (issue, comment, run, document) produced this memory.

**Steal:** Whatever memory store we use, every memory entry must point back to the durable work object that created it. Without that, you cannot reason about staleness, contradiction, or trust.

### 4.3 Skills as the "explicit" memory layer

Skills (see §6) are essentially Paperclip's **explicit, governed, versioned memory**. They are how knowledge that the team agrees on gets into agents reliably, vs. the implicit memory of conversation/context which is opaque and unreliable.

**Steal:** Distinguish three layers:
- **Tacit memory** (model context, transient) — fast, cheap, untrustworthy
- **Episodic memory** (run logs, comments, activity) — durable, queryable, raw
- **Explicit memory** (skills, instructions, AGENTS.md) — versioned, governed, deliberately authored

Long-running agents fail when these three blur together.

---

## 5. Evolution & Self-Improvement

This is the area Paperclip is *least* opinionated about — which is itself instructive — but the seeds are there.

### 5.1 Bundles, not models, as the unit of evaluation

From [`agent-evals-framework.md`](../doc/plans/2026-03-13-agent-evals-framework.md):

> A bundle is: adapter type + model id + prompt template(s) + bootstrap prompt + skill allowlist / version + relevant runtime flags. That is the right unit because that is what actually changes behavior in Paperclip.

Most eval frameworks evaluate prompts or models. Paperclip plans to evaluate the **whole bundle** because in production that's what changes. This is the right framing for self-evolving agents: the unit of mutation and the unit of evaluation must be the same.

**Steal:** Define a "bundle" type early. Make the eval harness compare bundles. Make every change to an agent — model swap, prompt edit, new skill — produce a new bundle version that gets scored against the previous one.

### 5.2 Four-layer scoring

1. Deterministic contract evals (no judge model)
2. Structured rubric scoring
3. Pairwise candidate-vs-baseline judging (more reliable than open-ended scoring per OpenAI)
4. Efficiency metrics from normalized usage/cost telemetry

**Steal:** Don't trust LLM judges with absolute scores. Trust them with pairwise comparisons. Combine with deterministic contract checks.

### 5.3 Smart model routing as adapter-local, not global

[`smart-model-routing.md`](../doc/plans/2026-04-06-smart-model-routing.md) explicitly rejects the temptation to build a global server-side router. Instead, supported adapters get an opt-in "cheap preflight" phase: a cheap model orients to the wake, posts a progress comment, does light triage; the primary model does substantive work. The cost ledger learns to record multiple models per run.

**Steal:** The "right" routing decision depends on adapter and task shape; resist the architect's urge to centralize. Let each adapter own its routing, but standardize **how cost is reported across multiple models in one heartbeat**.

### 5.4 Skills-as-evolution-vector

Skills are first-class, versioned, importable from many sources (filesystem, GitHub, plugin packages), and **bound to companies and agents**. A skill update propagates to every agent that has it. This is essentially "deploy a behavior change to the whole org" — the right granularity for evolving a long-running team.

See: [`company-skills.ts`](../server/src/services/company-skills.ts), [`plugin-managed-skills.ts`](../server/src/services/plugin-managed-skills.ts).

**Steal:** Make the unit of capability a *package* (skill), not a monolithic system prompt. Then "the agent learned X" becomes "we shipped skill X v2" — auditable, revertable, and shareable across agents.

---

## 6. Skills system — explicit governed capabilities

The skills system deserves its own section because it's a clean answer to a hard question: *how do you give agents capabilities without giving them everything?*

Key design moves:

- **Skills are content + metadata + file inventory**, persisted per-company. Trust level (`trusted` vs `untrusted`) and source type (`filesystem`, `github`, `plugin`, ...) are first-class.
- **Skills can come from many sources** — local folders, git repos, plugin packages — but always normalized to the same shape.
- **Plugin-managed skills** carry a `paperclipManagedResource` marker so the system knows the canonical key, defaults, and drift detection. Plugins can ship skills as part of their manifest.
- **Project scans** look for skills in `.agents/skills`, `.claude/skills`, `.codex/skills`, `.cursor/skills`, etc. — interoperability with every coding-agent ecosystem out there.
- **Compatibility** is declared; the system knows which agent/adapter types a skill works with.
- **Trust gates**: untrusted skills can be installed but require explicit acknowledgement.

**For us:** This is the *cleanest* mechanism I've seen for a "skill library" that crosses agent vendors. If we want our agents to share, learn, and inherit capabilities — and to evolve via ship-a-new-skill rather than ship-a-new-prompt — this is the model.

---

## 7. Governance

### 7.1 Approvals as a typed object

Five kinds: `hire_agent | approve_ceo_strategy | budget_override_required | request_board_approval | …`. Each has a payload, decision note, decided-by user, decided-at timestamp, and a state machine (`pending → revision_requested → approved/rejected/cancelled`).

The approval *causes side effects* on resolution: approving a hire activates the agent and creates the budget policy; rejecting it terminates the pending agent. The hire-approved hook then **invokes an adapter-defined `onHireApproved` hook** — and if it fails, that's logged but **not fatal**. The approval still resolves cleanly.

**Steal:** Governance decisions and side effects must be separated, and side effects must be allowed to fail without rolling back the decision.

### 7.2 Budgets with hard stops

Budget policies are scoped (`company | agent | project`), have a window kind (`calendar_month_utc | lifetime`), an amount, and a warn percent. When observed spend ≥ amount, the system **auto-pauses the scope** and creates an approval for the override. The pause is real (cancels work via `cancelWorkForScope` hook) — not just a flag.

**Steal:** A budget that doesn't actually stop work is theater. Make the enforcement a real hook with side effects, and make resuming require an approval.

### 7.3 Pause holds on issue subtrees

You can pause an entire issue subtree (with a `releasePolicy: { strategy: 'manual' | ... }`), and the pause holds members. While paused, no recovery, no wakes. This is the right primitive when a manager wants to "freeze" an effort without cancelling it.

**Steal:** A subtree-scoped pause is the right tool for "stop everyone working on X, but don't lose state." Don't try to express it via individual statuses.

### 7.4 Activity log as the universal funnel

[`activity-log.ts`](../server/src/services/activity-log.ts) is small, but it does three things at once:

1. Persists a redacted audit row to `activity_log`
2. Publishes a live event for the UI
3. Maps the activity action to a typed plugin event and emits it on the plugin event bus

One write, three downstream consumers. This is the right shape — don't make plugins poll, don't make the UI poll, don't make audit a separate code path.

**Steal:** Single write site → fan out to audit, UI, plugins. Define a tight allow-list of mappable event types so plugins can't subscribe to everything by accident.

---

## 8. Hierarchy & Org Structure

### 8.1 Strict tree, single manager

V1 explicitly chose a strict tree (`reports_to` nullable root, no multi-manager reporting). Cycles are prohibited. This is a deliberate simplification: fancier graph topologies are *much* harder to govern.

**Steal:** Start with a strict tree. Add matrix reporting only when you have a forcing function and a clear governance story.

### 8.2 Goal hierarchy as the alignment spine

Every task must trace through `parentId` / `goalId` / project-goal linkage to the company goal. The example from `PRODUCT.md`:

```
Researching Facebook ads Granola uses
  → Create FB ads for our software
    → Grow signups by 100
      → Get revenue to $2k this week
        → ...
          → Build #1 AI note-taking app to $1M MRR in 3 months
```

For long-horizon agents this is the **anti-drift mechanism**. An agent two months in can always answer "why am I doing this?" by walking up the chain. Without it, agents slowly wander.

**Steal:** Every work item must have a parent that traces to the top goal. Enforce this at create time, not as a lint.

### 8.3 Capabilities as discoverable text

Each agent has a freeform **capabilities** description — a short paragraph on "what this agent does and when they're relevant." Other agents (and humans) read it to decide who to delegate to. Simple, low-tech, surprisingly effective.

**Steal:** A free-text capability blurb beats a structured taxonomy at this scale, because it's both human-readable and LLM-readable. Don't over-formalize too early.

---

## 9. Adapter / Plugin Architecture

### 9.1 Adapter = "be callable"

The minimum contract for an agent runtime is just "be invocable, observable, cancellable." The repo supports five wildly different invocation models behind the same interface:

1. Local CLI/session (Claude Code, Codex, Cursor, OpenCode, Gemini, Pi)
2. Generic shell process
3. HTTP/webhook fire-and-forget
4. OpenClaw gateway
5. External plugin adapters loaded dynamically

The adapter handles its own session state (resumable!), its own model selection, its own quota windows, its own hire-approval hook.

**Steal:** Define your harness's "agent runtime" as a tiny interface (`invoke`, `cancel`, `observe`, `getQuota?`, `detectModel?`, `onHireApproved?`). Then *everything* — local CLIs, hosted APIs, HTTP webhooks, even nested Paperclip-style sub-orgs — can plug in.

### 9.2 Zero hardcoded provider knowledge in core

The fork notes ([`AGENTS.md`](../AGENTS.md) §11) make this explicit:

> The plugin-loader should have ZERO hardcoded adapter imports — pure dynamic loading.

This is hard discipline but pays off massively over time. When core has zero knowledge of any specific provider, **you can never get stuck** when a provider changes API.

**Steal:** Make the adapter loader strict about this from day one. It's much easier to enforce than to retrofit.

### 9.3 Plugin lifecycle as a state machine + coordinator pattern

`plugin-lifecycle` is a state machine. `plugin-job-coordinator` listens to lifecycle events and (1) syncs job declarations from the manifest into the DB, (2) registers/unregisters with the scheduler. The same pattern is used for the heartbeat service coordinating timers and runs.

**Steal:** "Coordinator" is a clean name for the glue between independent services. Don't let services know about each other directly; let them emit events and have coordinators do the wiring.

---

## 10. Observability that fits long horizons

### 10.1 Heartbeat runs as the unit of observability

Every wake creates a `heartbeat_runs` row with status, started/finished, error, external run id, and a `context_snapshot` blob. Cost events join to it. Activity log joins to it. The UI shows live runs and recent runs per agent.

For a long-running harness, this is the right unit: **not "the agent" but "this wake of this agent."** You can answer "what did this agent do at 3am last Tuesday" precisely because runs are first-class.

### 10.2 Live events on a single bus

`publishLiveEvent` → WebSocket → UI. One bus, typed events, company-scoped. Plugins use the same bus.

### 10.3 Log redaction as an instance setting

[`log-redaction.ts`](../server/src/services/../log-redaction.ts) lets the operator turn on "censor current user in logs." Small but significant — long-horizon logs accumulate enough PII that you eventually want to make it a switch, not an audit.

---

## 11. Things that are refreshingly simple

A few small choices that punch above their weight:

- **Heartbeat env vars.** `PAPERCLIP_AGENT_ID`, `PAPERCLIP_COMPANY_ID`, `PAPERCLIP_API_URL`, `PAPERCLIP_RUN_ID`, plus optional wake context. The agent doesn't need to "log in" — the env hands it identity. Run JWTs for local adapters are short-lived and auto-injected.
- **`X-Paperclip-Run-Id` header on every mutation.** Any change the agent makes is automatically traceable to the heartbeat that made it. The skill enforces it; the server uses it for audit.
- **Idempotency keys on wakes and recovery actions.** Per-issue uniqueness for active recoveries. Prevents storms.
- **`updatedAt` touch on issue mutation** so caches and wakes can detect change without a full diff.
- **A 200-line cron parser** instead of pulling in a dependency, because the supported syntax is exactly what's needed and no more.
- **Quota windows polled across all adapters with `Promise.allSettled` + per-adapter timeout.** One slow provider can't take down the whole quota view. ([`quota-windows.ts`](../server/src/services/quota-windows.ts))
- **Run audit-trail header documented in the agent's own skill.** The agent enforces its own audit because the skill says to. Convention over runtime enforcement, where appropriate.

---

## 12. Things to argue with / avoid

Not everything in Paperclip is something we should copy:

- **Single-tenant V1.** The data model is multi-company but the deployment is single-tenant. That's a deliberate scope cut for a control plane shipped as a product. For a harness intended to host *one* company per deployment, fine. For a multi-tenant SaaS, that's V2 work.
- **Strict tree only.** Real orgs have matrix reporting eventually. Just be aware you're picking the simpler primitive on purpose.
- **Tasks-only communication, no chat.** This is correct for board-level governance but can feel constrained when agents legitimately need short, informal coordination. The product line is "chat lives as plugins."
- **Postgres-only persistence.** Embedded PGlite for dev is great, but the path to other backends doesn't exist. For our harness, this is fine; for a library, it's not.
- **Whole-system polling for stuck runs.** Effective and simple but won't scale to thousands of agents per instance without rework.

---

## 13. Synthesis — a starter blueprint for our harness

If we wanted a one-page distillation of what to take, it's this:

1. **Identity**: agents have a durable id; processes don't. Wake/run is the unit.
2. **Heartbeats**: short, bounded, coalesced, with typed wake payloads carrying the delta.
3. **Tasks**: single-assignee, atomic checkout, two-lock model (ownership + execution), traced through goal hierarchy to a top-level mission.
4. **Liveness**: every non-terminal task must have one of N typed action paths or it's flagged stalled.
5. **Three invariants** carved into the wall: productive work continues, only real blockers stop work, no infinite loops. Every new rule shows its work against all three.
6. **Recovery as a typed object** with owner, cause, evidence, next action, bounded retries, escalation policy, and resolution outcome. Source-scoped by default; issue-backed only when the repair is independent work.
7. **Memory as a binding layer**, not an engine. Six-op portable core. Provider-owned extraction. Provenance is mandatory.
8. **Skills** as the explicit, versioned, governed capability layer. Source-of-truth for "how does this agent work" — not the system prompt.
9. **Bundles** (adapter+model+prompt+skills+runtime flags) as the unit of evolution and the unit of evaluation. Pairwise judging + deterministic contract checks + cost telemetry.
10. **Adapter contract** so small that any runtime can plug in. Zero hardcoded providers in core.
11. **Activity log → live events → plugin events** as a single funnel from one write site.
12. **Approvals + budgets + pause holds** as real, side-effect-bearing primitives, not flags.
13. **Cron produces work items**, not direct invocations. Routines have their own revisions and runs.
14. **Productivity review + silent-run watchdog**: separate signals beyond "is the process alive."

---

## Suggested next reads (file-by-file deep dive)

When we move to the second pass (file-by-file), I'd order it like this:

1. [`doc/SPEC-implementation.md`](../doc/SPEC-implementation.md) — the V1 contract, the data model
2. [`doc/execution-semantics.md`](../doc/execution-semantics.md) — the liveness contract in full
3. [`server/src/services/heartbeat.ts`](../server/src/services/heartbeat.ts) — the central orchestrator
4. [`server/src/services/recovery/service.ts`](../server/src/services/recovery/service.ts) + the rest of `recovery/` — every recovery shape
5. [`server/src/services/issues.ts`](../server/src/services/issues.ts) + [`issue-execution-policy.ts`](../server/src/services/issue-execution-policy.ts) + [`issue-tree-control.ts`](../server/src/services/issue-tree-control.ts) — the task model
6. [`server/src/services/productivity-review.ts`](../server/src/services/productivity-review.ts) + run-liveness modules — watchdog patterns
7. [`server/src/services/company-skills.ts`](../server/src/services/company-skills.ts) + `plugin-managed-skills.ts` + `plugin-managed-agents.ts` — the skill/plugin extension surface
8. [`server/src/services/budgets.ts`](../server/src/services/budgets.ts), `approvals.ts`, `activity-log.ts` — governance
9. `packages/adapters/*` — the adapter contract and every reference implementation
10. [`doc/memory-landscape.md`](../doc/memory-landscape.md) + [`doc/plans/2026-03-17-memory-service-surface-api.md`](../doc/plans/2026-03-17-memory-service-surface-api.md) — memory thinking
11. [`doc/plans/2026-04-08-agent-os-technical-report.md`](../doc/plans/2026-04-08-agent-os-technical-report.md) — adjacent runtime ideas (capability vocabulary, snapshotted FS) we may want
12. [`skills/diagnose-why-work-stopped/SKILL.md`](../skills/diagnose-why-work-stopped/SKILL.md) — the operational doctrine, distilled

That order takes us from the contract → the orchestrator → the recovery/liveness machinery → the extension surface → the governance → the adapters → the ideas they're still chewing on.
