# 08 — Task Claim & Lease

**One-liner:** When more than one runner can pull work from the shared exec-plan board, a task is
claimed with **two locks** — a durable ownership token (`checkout_run_id`) and an ephemeral,
heartbeat-renewed liveness token (`execution_run_id`) — held in a fast CAS coordination store
(`board.sqlite`, WAL) separate from git; a live lease is respected (**409-never-retry**), an expired
lease is reclaimable via CAS (exactly-one-wins), and a global semaphore bounds concurrency. *Graceful
recovery from a dead runner — reconciliation with a grace window, the typed resume/restart/abandon
decision, and the liveness tri-state — is the sibling spec `#10p5`, built once the swarm that
exercises it exists.*

> **Split note.** This spec was divided from the original `08-claim-recovery-liveness`. **08 (here)** is
> the *correctness floor* multi-runner work needs: who owns a task, is the lease live, who may run
> concurrently. **`#10p5` (Runner Recovery & Liveness)** is the *resilience layer*: what happens when a
> runner dies. 08 is a hard prerequisite for `#10` (swarm); `#10p5` is built after `#10` exists so its
> recovery/liveness integration is shaped by a real consumer rather than speculation.

**Sources (source of truth):** `docs/specs/new-specs/13-claim-recovery-liveness.md` — the two-lock
claim protocol, the single shared coordination store (`board.sqlite`, WAL) as the *only* sanctioned
cross-worktree mutable state and the narrow exception to `#02`'s no-worktree-to-worktree rule, the
durable git mirror written only at claim boundaries, the lease = `heartbeat_interval × miss_tolerance`
model, the 409-never-retry rule, the per-session serialisation + global concurrency cap, and the
claim/heartbeat/release acceptance criteria are carried forward verbatim-in-substance here. That
spec's claim lineage (paperclip two-lock checkout · hermes SQLite-WAL CAS · nanobot per-session lock +
semaphore) is the conceptual authority. The reconciliation/recovery/liveness portions of that
conceptual spec move to `#10p5`. · `#02` (checkpoint refs, worktree fast-resume, atomic writes,
`exclusive_file_lock` — reused as-is; the coordination store is the one explicit exception to
per-worktree isolation) · `#07` (the exec-plan ledger gains a `claim` field) · `#10` (the swarm is the
consumer that justifies the claim) · `#10p5` (recovery + liveness ride on the claim/lease defined
here).

**Reference (grounding only, not authority):** [openharness] OpenHarness has **no two-lock
coordination store** — it is single-driver per worktree — so it grounds nothing in the *claim
protocol*; the recovery seam it does ground (`has_pending_continuation`,
`sanitize_conversation_messages`, `create_worktree` fast-resume) belongs to `#10p5`, not here. The
claim/lease/concurrency machinery in this spec is new substance from the conceptual sources, which
OpenHarness does not provide.

---

## Why this matters

Specs `#02` and `#07` assume a benign world: a scheduler allocates a `task-id`, hands it to one
runner, and that runner is the only process that ever touches it. `#02` even states "tasks coordinate
only through committed state" and "concurrency is per-worktree." That is correct for *isolation* but
silent on *coordination*. The moment more than one runner can pull work from the shared exec-plan
board (`#07`, driven by `#09`/`#10`) one question appears that nothing else answers:

> **Who owns this task right now, and is that owner still alive?**

The conceptual spec converged its inspiration sources onto one anatomy: a *durable ownership claim*
plus an *ephemeral liveness token*, held in a fast CAS store. And it makes one honest bend to the
founding bet: **git is the system of record, but git is a poor concurrency primitive** — you cannot
cheaply compare-and-swap on a ref across processes, and commit conflicts are a human-resolved mess. So
it splits *what the harness persists* (git: low write rate, auditable) from *how it coordinates* (a
fast CAS store: high write rate, ephemeral). That split is the single new idea; everything else here
is mechanism.

This spec answers ownership and liveness *tokens*. The follow-on question — "a runner died; do we
resume, restart, or abandon?" — is `#10p5`. The cut is deliberate: a swarm is *correct* with just the
claim floor (no two live owners; a dead owner's lease expires and the task is reclaimed and restarted
fresh). `#10p5` makes that reclaim *graceful* (resume from a checkpoint instead of restarting; never
murder a merely-slow runner; give up safely after repeated failures).

The three invariants (paperclip) stay load-bearing even at this floor: productive work continues
(claims free up on lease expiry), only real blockers stop the agent, no infinite loops.

## Scope

**In:** the two-lock claim protocol; the coordination store (`board.sqlite`, WAL) and its base
schema; claim leases + heartbeat renewal; the 409-never-retry rule; reclaim of an expired lease as a
CAS (exactly-one-wins); the durable git mirror at claim boundaries; per-session serialisation +
global concurrency cap; clean release.

**Out:** reconciliation order, the grace window, the typed recovery object and its decision rule,
auto-block after repeated failures, the liveness tri-state, phase-specific timeouts, kill-backoff, and
the recovery-related schema columns (→ `#10p5`); what a turn does internally (→ `#03`); checkpoint
refs and worktree teardown (→ `#02`, reused as-is); the task ledger format and cron kinds (→ `#07`);
model/provider failover (→ `#02`/`#11` — orthogonal: provider death vs runner death); multi-host
distribution (deferred — single-host in v1, per `#02`); the loop that schedules the work (→ `#09`,
`#10`).

## Key decisions (assumed defaults)

1. **Two locks per claim.**
   - `checkout_run_id` — **ownership**. Minted when a runner wins a claim. Durable.
   - `execution_run_id` — **liveness**. Minted at session start inside the worktree,
     heartbeat-renewed. Ephemeral.
   They are separate so "I own this" and "I am alive right now" can diverge — and that divergence is
   the trigger `#10p5` acts on.

2. **One shared coordination store: `.dream/coordination/board.sqlite` (WAL mode).** The *only*
   sanctioned cross-worktree mutable state, the explicit narrow exception to `#02`'s
   no-worktree-to-worktree rule. Holds claim rows. Git-ignored. CAS/read only — never a general
   message bus. (The path helper `DreamPaths.coordination_board` already exists from `#01`.)

3. **Durable ownership also lands in git.** On claim grant and on release/handoff, the runner writes
   the ownership facts into the task ledger (`#07`) field `claim` and commits. Low write rate (claim,
   release, reclaim only — never per heartbeat), so git never absorbs heartbeat traffic.

4. **Claim lease = `heartbeat_interval × miss_tolerance`.** Defaults: `heartbeat_interval` 15s,
   `miss_tolerance` 60 → **15-minute lease**. A claim is respected while its lease is unexpired; each
   heartbeat renews it. Long lease tolerates GC pauses and long tool calls; short heartbeat gives fast
   crash detection.

5. **409-never-retry.** A runner that tries to claim a task whose lease is *unexpired* and owned by
   someone else receives a denial and **must not retry that task** — it picks the next available task
   from the board. (paperclip)

6. **An expired lease is reclaimable via CAS.** When `lease_expires_at ≤ now`, the task is eligible to
   be claimed again: a runner mints a fresh `checkout_run_id` under `BEGIN IMMEDIATE`; exactly one
   wins, the other gets a 409 and moves on. *At this floor, reclaim simply restarts the task with a
   fresh claim.* The graceful path — a grace window before declaring `lost`, and a typed
   resume/restart/abandon decision instead of a blind restart — is `#10p5`.

7. **Per-session serialisation + global concurrency cap.** At most one execution per `task-id` at a
   time (the claim enforces it); a global `max_concurrent_runs` semaphore bounds org-wide parallelism.
   (nanobot)

8. **Injectable clock.** Lease expiry is strictly wall-clock (`lease_expires_at ≤ now`). All "now"
   reads go through a small injected `Clock` (`#01`-style util) so tests advance time deterministically
   and `#10p5`'s grace/stall windows reuse the same seam. Single-host v1 makes wall-clock safe;
   multi-host monotonic/lease-server is deferred (`#02`).

## Artefact shapes

### Coordination store base schema (`.dream/coordination/board.sqlite`, WAL)

Table `claims` (the columns this spec owns):
- `task_id` TEXT PRIMARY KEY
- `checkout_run_id` TEXT — current ownership token (null = unclaimed)
- `execution_run_id` TEXT — current liveness token (null = claimed but not yet executing)
- `claimed_by` TEXT — runner/host identifier
- `claimed_at` INTEGER — epoch ms
- `lease_expires_at` INTEGER — epoch ms; renewed on heartbeat
- `last_heartbeat_at` INTEGER — epoch ms
- `state` TEXT — `claimed | executing | releasing`

All transitions use SQLite `BEGIN IMMEDIATE` (write CAS); the durable mirror in git is written *after*
the CAS commits, never instead of it. **`#10p5` extends this table** with `last_progress_at`,
`last_phase`, `claim_failures`, and the `lost | blocked` states.

### Durable ownership mirror (ledger `{task-id}.json`, `#07`)

```json
"claim": {
  "checkout_run_id": "co_01H...",
  "claimed_by": "lane-backend@host-3",
  "claimed_at": "2026-06-03T04:20:00Z",
  "released_at": null
}
```
Written on grant, release, and reclaim only. (`#10p5` adds `recovery_count`.)

## Behaviours

### Claiming a task

1. Runner selects a candidate `task_id` from the board (unclaimed, or lease-expired).
2. Runner opens `BEGIN IMMEDIATE` on `board.sqlite`, re-reads the row.
3. If a live owner exists (lease unexpired, different `checkout_run_id`) → **409**: rollback, pick a
   different task (never retry this one — decision #5).
4. Otherwise mint a fresh `checkout_run_id`, set `state=claimed`, `claimed_by`, `claimed_at`,
   `lease_expires_at = now + lease`, commit the CAS. (Reclaiming an expired lease follows the same
   path; `#10p5` inserts a recovery decision before this acts.)
5. Mirror ownership into the ledger (`#07`) and commit to git.
6. Proceed to `#02` task-start (worktree + sidecars), then session start (`#03`).

### Heartbeat (during execution)

1. At session start, mint `execution_run_id`, set `state=executing` via CAS.
2. Every `heartbeat_interval`, CAS-update `last_heartbeat_at` and `lease_expires_at = now + lease`.
3. Heartbeat is fire-and-forget; a failed heartbeat write is logged-as-data but does not abort the
   turn (the lease simply ages until the next successful beat). *Progress-signal tracking
   (`last_progress_at`, `last_phase`) for liveness is `#10p5`.*

### Acquiring a concurrency slot

1. Before `claimed → executing`, acquire the global `max_concurrent_runs` semaphore; release it on
   task end. The claim enforces one run per task; the semaphore bounds total parallelism.

### Clean release

1. On task end (success/failure, `#02`), CAS `state=releasing` then clear `checkout_run_id`/
   `execution_run_id`, set the ledger `claim.released_at`, commit git.
2. Crash before release is exactly what `#10p5` reconciliation handles — release is best-effort.

## Acceptance criteria

### Two-lock claim (MUST)

1. **MUST** mint distinct `checkout_run_id` (ownership) and `execution_run_id` (liveness) tokens, the
   former on claim and the latter on session start.
2. **MUST** perform every claim/reclaim as a CAS transaction against `board.sqlite`
   (`BEGIN IMMEDIATE`), re-reading the row inside the transaction.
3. **MUST** mirror ownership to the ledger (`#07`) and commit on grant, release, and reclaim only —
   never on heartbeat.
4. **MUST** keep `board.sqlite` git-ignored and use it only for claims/liveness — never as a general
   message bus.

### 409-never-retry & concurrency (MUST)

5. **MUST** deny a claim on a task whose lease is unexpired and owned by another `checkout_run_id`.
6. **MUST NOT** retry a denied (409) task in the same selection pass; the runner picks a different
   task.
7. **MUST** enforce at most one executing run per `task_id` via the claim.
8. **MUST** bound concurrent runs by a global `max_concurrent_runs` semaphore.

### Heartbeat & lease (MUST/SHOULD)

9. **MUST** renew `lease_expires_at` on every successful heartbeat.
10. **MUST** treat a lease as expired strictly by wall-clock (`lease_expires_at ≤ now`).
11. **SHOULD** tolerate transient heartbeat-write failures without aborting the turn.
12. **MUST** allow an expired lease to be reclaimed via the same CAS path, exactly-one-wins.

### Release (MUST)

13. **MUST** clear both tokens and set the ledger `claim.released_at` on clean release.

## Acceptance scenarios

```gherkin
Scenario: Live owner blocks a second claimant (409, no retry)
  Given task T1 is claimed by checkout co_A with an unexpired lease
  When runner B attempts to claim T1
  Then runner B receives a 409 denial
  And runner B does not retry T1 in the same pass
  And runner B proceeds to select a different task.

Scenario: Heartbeat renews the lease
  Given task T1 is executing with lease expiring in 15 minutes
  When the runner heartbeats every 15 seconds
  Then lease_expires_at advances on each heartbeat
  And T1 is never eligible for reclaim while the runner is alive.

Scenario: Expired lease is reclaimable, exactly one winner
  Given task T1's lease has expired
  When runners B and C both attempt to reclaim T1 simultaneously
  Then exactly one mints a fresh checkout_run_id under BEGIN IMMEDIATE
  And the other receives a 409 and moves on.

Scenario: One executing run per task
  Given task T1 is executing as ex_A
  When a second claim for T1 is attempted while the lease is live
  Then it is denied and no second execution starts.

Scenario: Global semaphore bounds concurrency
  Given max_concurrent_runs is 3 and three tasks are executing
  When a fourth claimed task tries to start executing
  Then it waits for a slot rather than exceeding the cap.

Scenario: Durable mirror written only at claim boundaries
  Given T1 runs for an hour heartbeating every 15s
  When the runner completes T1
  Then the ledger claim field was committed on grant and on release only
  And no per-heartbeat commits exist in git history.

Scenario: Clean release clears both tokens
  Given T1 finishes successfully
  When it releases
  Then checkout_run_id and execution_run_id are cleared
  And the ledger records claim.released_at.
```

## Tests

- `test_distinct_ownership_and_liveness_tokens` — two locks minted at different points.
- `test_claim_is_cas_transaction` — concurrent claimants, exactly one wins.
- `test_live_owner_denies_second_claim_409` — lease-respecting denial.
- `test_denied_claim_not_retried_same_pass` — 409-never-retry.
- `test_expired_lease_reclaim_exactly_one_winner` — reclaim CAS.
- `test_one_executing_run_per_task` — single-assignee invariant.
- `test_global_semaphore_bounds_concurrency` — org-wide cap.
- `test_heartbeat_renews_lease` — lease advances on beat.
- `test_lease_expiry_is_wallclock` — strict expiry (via injected clock).
- `test_transient_heartbeat_failure_does_not_abort_turn` — resilience.
- `test_durable_mirror_written_only_at_claim_boundaries` — no per-heartbeat commits.
- `test_board_sqlite_is_git_ignored` — coordination store hygiene.
- `test_clean_release_clears_both_tokens` — release path.
- `test_clock_is_injectable_for_lease_math` — deterministic time seam.

## Edge cases

- **Clock skew between hosts.** v1 is single-host (`#02`), so wall-clock leases are safe. Multi-host
  must move to a monotonic/lease-server scheme — flagged, deferred.
- **`board.sqlite` corrupted or deleted.** Rebuildable from the durable ledger mirrors at startup:
  scan `active/*.json` `claim` fields, treat any with no live process as expired. The git mirror is
  the source of truth *of record*; the board is the source of truth *of now*.
- **Two runners both observe an expired lease simultaneously.** The reclaim is itself a CAS (mint new
  `checkout_run_id` under `BEGIN IMMEDIATE`); exactly one wins, the other gets a 409 and moves on.
- **`max_concurrent_runs` reached while a high-priority task waits.** v1: FIFO by board selection
  order; priority queuing is deferred (consistent with `#07`).
- **A runner heartbeats but makes no progress forever (live-but-useless).** *Not* caught at this
  floor — the lease stays fresh. This is exactly why `#10p5` adds the liveness tri-state.

## Open questions

- Whether the coordination store should be SQLite-WAL (simple, single-host) or a small lease server
  from day one (needed for multi-host, `#02`'s deferred direction).
- Whether the global semaphore lives in `board.sqlite` (durable, cross-process) or in-process only
  (simpler; sufficient for single-host swarm).

## Out of scope

- Reconciliation, the grace window, the typed recovery object, resume/restart/abandon, auto-block,
  the liveness tri-state, phase timeouts, and kill-backoff (→ `#10p5`).
- Turn-internal FSM and its records (→ `#03`; consumed by `#10p5`, not here).
- Checkpoint refs, worktree creation/teardown, atomic writes (→ `#02`; reused as-is).
- Task ledger format, plan states, cron kinds (→ `#07`; this spec adds only the `claim` field).
- Model/provider failover and cooldowns (→ `#02`/`#11`).
- Multi-host claim distribution and cross-host clock authority (deferred — single-host v1).
- The autopilot/swarm loops that schedule and run the work (→ `#09`, `#10`).
