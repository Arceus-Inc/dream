# The Harness, Told Through Its Four Clocks

> A synthesis of six inspiration sources (nanobot, hermes, paperclip, openclaw, voyager,
> openharness) against the `docs/specs/new-specs/` specs. The framing device: each source is
> really answering a different *timescale* question, and the unresolved tensions in the
> specs only sort themselves out once you order them by clock speed. The sixth source,
> OpenHarness, is unusual: it is the only one that shipped the *whole* sprint clock as
> running code, so it acts less as a new idea and more as a working reference implementation
> that confirms — and sharpens — the others.

---

## Why a story, and why clocks

Read six agent codebases back to back and it is tempting to think they are six answers
to the same question. They are not. They are six answers to **four different questions**,
nested inside each other like clocks of different speeds:

- **Nanobot** thinks in *seconds* — what happens inside a single turn.
- **Hermes** and **paperclip** think in *hours* — what happens when a process dies and
  another must pick up its work.
- **OpenClaw** thinks in *days* — how a stream of stakeholder pings becomes orderly work
  without a planner-of-planners.
- **Voyager** thinks in *months* — how an agent gets *better* without a human in the loop
  and without drowning in its own history.
- **OpenHarness** is the odd one out — it does not think in a new timescale, it
  *operationalises the day clock end to end*: it is the only source that shipped a complete
  autonomous repo-work loop (intake → score → run → **verify → PR → CI → repair → merge**)
  as running code. Where the others give a principle, OpenHarness gives a transition table
  that actually ran, so it serves as the reference implementation that confirms the sprint
  clock and exposes the states the others left implicit.

The specs tried to answer all four at once. Where they feel thin, it is almost
always because a fast-clock concern got written down crisply and a slow-clock concern got
deferred to an "open question."

## The convergence (what the specs got right)

Six unrelated codebases independently landed on the same spine. That is the strongest
possible signal the specs' core bet is correct:

| Primitive | nanobot | openclaw | paperclip | hermes | voyager | openharness | → Spec |
|---|---|---|---|---|---|---|---|
| Repo/disk = system of record | git-tracked MEMORY/SOUL | all memory is markdown in git | memory-as-binding-layer | — | reconstruct-from-disk-only | append-only journal + rebuild-context | **01, 02** |
| Turn/loop FSM with trace | TurnState enum + transitions | lifecycle event streams | liveness action-paths | — | ~20-line loop | 13-state RepoTaskStatus FSM | **03, 14** |
| Two-phase consolidation (plan→act) | Dream P1/P2, cursor-on-success | Dreaming Light/Deep/REM | — | compactor schema | — | autodream plan→act, child-proc scoped | **10** |
| Progressive-disclosure skills | metadata/body/refs | identical 3-level | — | curator + pinning | code-as-skill top-K | — | **05** |
| Heartbeat ≠ daemon, structured wake | virtual-tool skip/run | heartbeat ≠ cron ≠ task | 4 wake sources coalesced | — | — | TaskType incl. dream + cron-as-session | **09** |
| Auth rotation → model fallback | — | model-scoped cooldown ladder | adapter contract | — | — | provider-conditional argv build | **11** |
| Capability minimisation | BLOCKED/READ_ONLY/RESTRICTED | Gateway allow/deny | — | tool guardrails | trusted primitives | path-scoped AllowedPath + perm-sync | **08** |
| Bounded retry, never infinite | MAX_INJECTION_CYCLES | 3-attempts-then-escalate | three invariants | auto-block after 5 | max_retries=4 + hard reset | max_attempts + failure-carrying repair | **03/09/14** |
| Worktree-per-task isolation | — | — | — | — | — | flat-slug worktrees + symlink common dirs | **02** |
| Scored work selection + dedup | — | scored memory gate | goal-hierarchy alignment | Kanban CAS claim | QA-cache dedup | fingerprint dedup + scored pick_next_card | **09, 14** |

The specs are codifying a convergent design, not inventing one. OpenHarness is the column
that fills in almost every row — not because it is the most original, but because it is the
one that *built* the loop, so it has an opinion everywhere the others only had one.

---

## The first clock: the turn (seconds to minutes)

A long-running agent spends most of its life *between* token calls, so the inter-turn
lifecycle should itself be a tiny state machine with a transition table, and every
transition should leave a `StateTraceEntry` on disk: which state, when, how long, what
event, what error (nanobot). Spec 03 has the FSM. What it lacks is the **flight-recorder
discipline** — the trace is not for debugging, it is for *resurrection*. A turn that died
at `SAVE` rather than `RUN` must be resumed differently, and you cannot know that unless
the trace told you where the body fell.

**Dropped primitive — mid-turn injection (nanobot).** A worker twelve tool-calls deep
holds LLM state that cost real money to build. Cancel the turn and you throw it away;
queue it as a fresh turn and you lose the urgency of *"actually, also consider X."*
Nanobot's answer: a bounded queue drained between iterations (≤3 injections/turn, ≤5
cycles), and — the load-bearing detail OpenClaw states even more sharply — you drain it
**at a model boundary, never inside a tool-call batch**.

That single invariant recurs at every clock. **The harness has exactly one rule it must
never break: a tool call and its result are an atom.** Honour it in the turn FSM, in the
compactor, and in the queue drain, and you eliminate an entire genus of provider-400 bugs
and corrupt transcripts.

Two more turn-clock primitives, both about *not lying to yourself*:

- **Critic-sees-outcome-not-code (voyager).** Promote from implicit to mandatory. Feed
  the evaluator diffs/metrics/artifacts, never the worker's own reasoning. The moment the
  grader can read the maker's justification, governance becomes theatre.
- **Execute-Verify-Report (openclaw).** One paragraph of plain English that kills the #1
  long-running failure — cheerful acknowledgement with no progress. *"'I'll do that' is
  not execution. 'Done' without verification is not acceptable. Prove it. Three attempts,
  then escalate."* Belongs verbatim in the system prompt and again in the governance file.

## The second clock: the session (hours, and the first crash)

The hard problem: **two agents want the same task, and one is about to die.** Three
sources give three implementations of one anatomy:

- **Hermes** — SQLite-WAL Kanban CAS: claim a card, 15-minute TTL, heartbeat to renew,
  auto-block the card after five failed spawns so a poisoned task can't eat the board.
- **Paperclip** — *two locks*: a `checkoutRunId` ("I own this") and an `executionRunId`
  ("I am alive and working right now"), plus the rule that a 409 (already owned) is
  **never retried** — you go find other work.
- **Nanobot** — per-session lock (consistency) + global semaphore (throughput).

Different sizes, one shape: a durable ownership claim, an ephemeral liveness token, and a
reconciliation rule for when they disagree.

**This opens a crack in the founding bet. Git is the system of record, but git is a
terrible concurrency primitive.** You cannot cheaply CAS on a ref across processes;
commits serialize but they also *conflict*, and a conflict is a mess a human resolves. So
the harness must split *what it persists* from *how it coordinates*:

- **Durable ownership claim** → can be a committed file in git (auditable; `git log` shows
  who owned what, when).
- **Ephemeral liveness token** → must live in a fast store (SQLite-WAL or a file lock),
  because it is heartbeat-renewed every few seconds and git was never built for that
  write rate.

When the two diverge — ownership says agent A holds the card, but A's liveness token went
stale 15 minutes ago — **that divergence is a typed recovery event, not an error.**

Paperclip's deepest idea, under-used by the specs: **recovery is a typed object.** A crash
is a first-class state with a first-class question — resume, restart, or abandon? —
answered by reading the turn's flight recorder. Reconciliation order is fixed across
OpenClaw and paperclip: **runtime-owned first, durable-history second.** Trust the runtime
if it still claims the task is running; only after a grace window consult the on-disk run
log and declare the task `lost`. Voyager supplies the bottom rung: wrap the whole rollout
in try/except, and on any panic do a **hard reset that preserves inventory/position**,
then continue. Translated: abandon the dead turn, keep durable state, never let one
panicked agent poison the company.

**Liveness tri-state (openclaw)** is a different axis from verification entirely. Spec 07
grades whether output is *good*; this grades whether the agent is *alive*:

- `long_running` — working, no problem.
- `stalled` — working, but no progress signal for N minutes.
- `stuck` — bookkeeping says busy, but nothing is happening.

Each backs off rather than murdering a merely-slow run (abort-drain only after 5× the
warning threshold). Pair with **phase-specific timeouts** so the signal is never the
useless "stuck for 48h" but the actionable "stalled before first model call, last phase:
context-engine." On a month-long dashboard, *stuck-because* beats *stuck-for* a hundred to
one.

**Dropped primitive — the 4-mode queue (openclaw):** `steer` (inject at next boundary),
`followup` (run after), `collect` (debounce + coalesce), `interrupt` (abort + take
newest). Same injection machinery as the turn clock, lifted to a per-channel policy: the
exec inbox gets `interrupt`, a chatty room gets `collect`, with no change to agent logic.
The harness must never refuse work because it is busy; dropping events on a long-lived
agent is a silent correctness bug.

## The third clock: the sprint (days, and the ledger)

The unit of work becomes a *task with a life of its own*. OpenClaw's separation is the one
to copy: **schedulers decide *when*; the task ledger records *what happened*; they are
different things.** A task moves `queued → running → terminal(succeeded | failed |
timed_out | cancelled | lost)`, and completion is **push-driven** — a finished task wakes
its owning session or pings a channel. Polling is usually the wrong shape. The ledger, not
any agent's memory, is the source of truth — the only way the system survives the agent
that was tracking the work crashing.

Above the ledger sits paperclip's **goal hierarchy as the anti-drift spine** — and note
this is *data*, not an agent hierarchy. A tree of OKRs/tasks against which every unit of
work is checked for alignment. (This is the hinge for the big fork below.)

The evaluator earns its keep here. Voyager's framing: **critique is a first-class prompt
slot on retry.** A retry is never the same prompt re-run; it is the same prompt + a
structured complaint. Output is schema'd JSON — `{success, reasoning, critique}` — and
that JSON, accumulated, *is* the governance log forever. Bounded retries (4 / 3) with
escalation at the end. Paperclip compresses the whole clock into **three invariants worth
carving above the door:** *productive work continues, only real blockers stop the agent,
no infinite loops.* Every retry cap, every 409-never-retry, every auto-block-after-five is
one of those three made concrete.

**OpenHarness is where this clock stops being principle and becomes a running machine.**
Its `autopilot` is the only inspiration source that shipped the *whole* sprint loop end to
end, and four of its decisions are the missing operational half of everything above:

- **The terminal state is `merged`, not `done`.** A thirteen-state FSM
  (`queued → accepted → preparing → running → verifying → pr_open → waiting_ci → repairing
  → completed → merged | failed | rejected | superseded`) makes "the worker stopped"
  (`completed`) and "the change is integrated" (`merged`) two different events with two
  different owners. Our spec 09's four-state ledger collapses them; the day clock lives
  almost entirely in the states 09 doesn't have.
- **Intake needs a dedup gate.** A `fingerprint` over normalised task content collapses the
  same issue arriving via GitHub, cron, and an operator ask into *one* card. Without it an
  autonomous intake loop quietly does the same work N times — the silent-duplication bug
  that only shows up once you let intake run unattended for a week.
- **Selection is scored, not FIFO — and re-scored on every transition.** `pick_next_card`
  sorts by `(-score, -updated_at, title)`, and the score is recomputed *every* time a card
  changes state, so the board continuously self-prioritises instead of draining in arrival
  order. This is OpenClaw's scored memory gate, lifted from "what to remember" to "what to
  work on next."
- **Repair carries the failure forward.** This is voyager's critique-on-retry made
  concrete: a failed verify or CI run does not re-run the original prompt — it threads
  `last_failure_stage` + `last_failure_summary` into a repair prompt whose fixed instruction
  is *"make the smallest patch that fixes the reported failure, do not restart from scratch,
  re-run the relevant checks."* And the journal is **append-only JSONL**, replayed (not
  hand-maintained) into a `rebuild_active_context` orientation artefact on every transition
  — so a freshly-woken runner orients in one read. CI itself is treated as a *feedback
  signal with three timers* (overall budget, no-checks grace, settle window), never a
  blocking wait that can hang.

This whole loop is now its own spec — see `docs/specs/new-specs/14-autonomous-repo-work-pipeline.md`
— sitting beside 09 (which 14 enriches) and 13 (which says *who* may run the loop
concurrently, where 14 says *what* the loop does).

## The fourth clock: the month (memory, evolution, and who is in charge)

All five sources converge on **memory-as-markdown-in-git**, because a months-long mind
must be diffable: `git log` is belief history, `git blame` answers "when did it start
believing this," `git revert` rolls back a bad consolidation, a PR is how a human approves
a belief change. Nanobot even feeds `git blame` ages *into* the consolidator as a
staleness prior, so the model sees which facts are old without being forced to delete
them.

Consolidation is the same **two-phase plan-then-act** everywhere: decide *what* should
change (no file tools), then a second pass with write tools scoped to one directory
applies the directives surgically; the cursor advances **only on success** so a crash
never loses or duplicates a batch. OpenClaw enriches it into a **scored gate** (frequency,
relevance, query-diversity, recency) plus a human-facing **Dream Diary** and a
**`promote-explain`** command that says exactly why a fact did or didn't get remembered.
The query-diversity weight is subtle: it stops one chatty thread from spamming permanent
memory with facts nobody recalls.

**Three dropped month-clock primitives the specs should reclaim:**

- **Commitments (openclaw)** — inferred, TTL'd, scoped follow-ups that live neither in
  memory nor on a calendar; scoped strictly to the agent+channel that made them, never
  delivered immediately, capped per day so an enthusiastic agent can't start a follow-up
  tornado.
- **QA cache (voyager)** — a self-built FAQ with vector dedup at a tight similarity
  threshold; over months a quiet but enormous cost saver because the agent stops re-paying
  to answer questions it already answered.
- **Two-occurrence rule (nanobot)** — a workflow becomes a skill only after it appears
  *twice*. The whole danger of self-evolution is one-shot weirdness hardening into
  permanent doctrine; "twice" is the cheapest guard.

Skill evolution is the same story in all five: progressive disclosure (description-only
index always in context, body on trigger, refs on demand), code-or-markdown payload, and a
**precedence ladder** where local scopes can *add* names but cannot *override* higher-trust
ones — so an evolving agent can never clobber a core safety skill with a same-named local
copy. Voyager adds the long-haul discipline: version-by-collision on disk with only the
latest reachable, top-K retrieval into the prompt every turn, and a boot-time consistency
assertion (`vectordb.count() == len(skills)`) that saves you at month four. The gap every
source admits, ours included: skills only ever get *added* — none garbage-collect.
**Prune-if-never-retrieved and merge-near-duplicates** are the missing half; nanobot's
git-derived per-line age annotation is the template.

## The fork at the top of the slowest clock

The question the twelve specs quietly declined to answer: **is the harness a tree of
agents, or a flat field of them?** Our dream-harness doc assumes a company hierarchy —
managers spawning workers, subagent depth cap 3. OpenClaw puts exactly that —
*"manager-of-managers, nested planner trees"* — under **"What We Will Not Merge,"** betting
instead on **flat specialist lanes**, each with a written contract (owns / does not own /
budget / handoff rule / tool posture). Their argument is bracing and probably correct:
parallelism only helps when it relieves a *real* bottleneck (session lock, rate limit,
tool capacity, context budget, ownership ambiguity) — and a nested planner relieves none
of those. It adds coordination cost and calls it structure.

**The resolution is the goal hierarchy. Separate the org model from the execution model.**
Hierarchy is real and necessary — but it belongs to *data and authority*, not *runtime
topology*:

- The **OKR tree** is a hierarchy.
- The **standing-orders document** that grants a role its scope, triggers, approval gates,
  and escalation path is a hierarchy. *A new hire is a new agent + a `STANDING-ORDERS.md`
  PR; a promotion is an edit to that file.* Git-diffable org policy.

But **execution is flat**: a field of specialist lanes pulling work off the shared Kanban
board, each holding its two-lock claim, each steerable by injection, none spawning a
runtime tree of children. Paperclip's one-liner dissolves the tension: **control plane,
not execution plane — agents phone home.** The manager is not a process that owns child
processes; it is a *role* whose standing-orders own a subtree of goals, and whose authority
is enforced **at the gateway** (allow/deny on tools, independent of personality) so even a
jailbroken worker is still blocked at the protocol layer. Do this and the
subagent-depth-cap-of-3 stops being a constraint you enforce and becomes a thing that
*cannot occur* — no agent spawns another agent; they all spawn against the board.

Memory falls in behind it: nanobot's single `MEMORY.md` becomes a **scoped resolver**
(`org → team → lane`) with the same asymmetric precedence as skills, so a lane reads the
org's beliefs but cannot rewrite them. The flat field shares a mind hierarchically without
nesting a single process.

**OpenHarness adds a real third option to this fork — and it is neither pole.** It does run
a hierarchy, but a *deliberately shallow* one: exactly one level, leader → worker, with no
manager-of-managers (it agrees with OpenClaw there). What makes it work without becoming a
runtime tree is two constraints OpenClaw's flat lanes and our nested tree both under-specify:

- **Per-member path-scoped contract.** Each `TeamMember` carries an explicit `AllowedPath`
  set — the worker is not trusted by personality, it is *fenced* to the files it may touch,
  and a `permission_sync` step has the leader propagate those scopes down. This is the
  "written contract" of OpenClaw's lanes, but enforced as capability rather than prose.
- **Typed bounded mailbox.** Coordination is a file-based queue
  (`inbox/<ts>_<id>.json`, atomic `.tmp`+rename) with a *closed* message vocabulary
  (`user_message | permission_request/response | sandbox_permission_request/response |
  shutdown | idle_notification`). It is a mailbox, not a message bus — a small typed
  alphabet, not free-form chatter — which is exactly the discipline that keeps a one-level
  hierarchy from degenerating into ad-hoc inter-agent RPC.

So the resolution sharpens: **flat-lanes vs nested-tree was a false binary.** The real knob
is *depth × contract strength*. A one-level leader→worker hierarchy is safe — even
preferable — when each member is path-fenced and talks through a typed bounded mailbox;
the danger OpenClaw rightly refuses is *unbounded* depth with *prose* contracts and
*free-form* coordination. Our spec's subagent-depth-cap-of-3 is the wrong lever; the right
levers are "one level, path-scoped, typed mailbox," and at that point depth polices itself.

---

## The whole shape, in one breath

- **Turn clock** → turn FSM, the tool-call atom, injection.
- **Session clock** → the two-lock claim, typed recovery, the liveness tri-state — and the
  honest admission that *git records but does not coordinate*.
- **Sprint clock** → the push-driven ledger, the goal spine, the critic that sees outcomes
  not code — and OpenHarness's full intake→score→run→verify→PR→CI→repair→merge machine that
  proves the clock by running it.
- **Month clock** → scored memory, the two-occurrence gate, skill GC, and the realisation
  that *hierarchy is data, not topology*.

The twelve specs got the convergent spine right. What's missing is the **motion** — the
primitives that keep work from being lost, agents from lying about progress, and tokens
from being re-spent — and the **honest seams**, the two places (coordination, and the org
fork) where the founding bet has to bend.

## What's now spec, and what's next

Two clocks have been turned into spec since this synthesis was first written:

- **Session clock → `docs/specs/new-specs/13-claim-recovery-liveness.md`.** The two-lock
  claim protocol (durable ownership + ephemeral heartbeated liveness), typed recovery
  (resume / restart / abandon), and the liveness tri-state — the load-bearing thing the
  specs were silent on and everything above it assumes works.
- **Sprint clock → `docs/specs/new-specs/14-autonomous-repo-work-pipeline.md`.** The
  OpenHarness autopilot loop: the thirteen-state repo-task FSM, fingerprint dedup, scored
  selection, the bounded failure-carrying repair loop, the append-only journal, CI as a
  three-timer feedback signal, and label-gated automerge governance. 14 says *what* the loop
  does; 13 says *who* may run it concurrently; together they make the day clock executable.

What remains, in order of load-bearing-ness: (1) **tier spec 13** — OpenHarness proves a
status-field guard + file-locked registry is enough for a single runner, so the two-lock
lease should be documented as the *multi-runner upgrade*, not the default; (2) **enrich spec
09's coarse four-state ledger** to reference 14's richer FSM rather than duplicate it; and
(3) the still-unwritten slow-clock primitives — skill GC, the two-occurrence rule, the QA
cache, and commitments — which every source admits and none fully ship.
