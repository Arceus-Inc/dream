# 08 — Task Claim, Recovery & Liveness

**One-liner:** When more than one runner can pull work from the shared exec-plan board, a task is
claimed with **two locks** — a durable ownership token (`checkout_run_id`) and an ephemeral,
heartbeat-renewed liveness token (`execution_run_id`) — held in a fast CAS coordination store
separate from git; when the two diverge, recovery is a **typed decision object** (resume / restart /
abandon) committed before it acts, not an exception; and liveness is a **tri-state**
(`long_running` / `stalled` / `stuck`), never a boolean.

**Sources (source of truth):** `docs/specs/new-specs/13-claim-recovery-liveness.md` — the entire
two-lock claim protocol, the single shared coordination store (`board.sqlite`, WAL) as the *only*
sanctioned cross-worktree mutable state and the narrow exception to `#02`'s no-worktree-to-worktree
rule, the durable git mirror written only at claim boundaries, the lease = `heartbeat_interval ×
miss_tolerance` model, the 409-never-retry rule, the fixed reconciliation order (runtime-owned-first,
durable-history-second, grace window), the typed recovery object + decision rule, auto-block after
`max_claim_failures`, the liveness tri-state with phase-specific timeouts and kill-backoff, and the
full acceptance criteria/Gherkin/tests are carried forward verbatim-in-substance and enriched here.
That spec's own lineage (paperclip two-lock checkout · hermes SQLite-WAL CAS + auto-block · openclaw
liveness tri-state + phase timeouts · nanobot per-session lock + semaphore · voyager
hard-reset-preserving-state) is the conceptual authority. · `#02` (checkpoint refs
`refs/harness/checkpoints/{task-id}/{n}`, worktree fast-resume, atomic writes, `exclusive_file_lock`
— reused as-is; the coordination store is the one explicit exception to per-worktree isolation) ·
`#03` (the turn FSM and its flight recorder `StateTraceEntry` — read to decide resume-vs-restart;
crash-resume of the conversation transcript) · `#07` (the exec-plan ledger gains a `claim` field;
abandon files a tech-debt entry and sets the task `blocked`) · `#11`/`#02` (model/provider failover
is orthogonal — that handles *provider* death, this handles *runner* death) · `#13` (the
`liveness.classified` and escalation events ride the hook bus).
**Reference (grounding only, not authority):** [openharness] OpenHarness has **no two-lock
coordination store** — it is single-driver per worktree — so it grounds only the *recovery seam*,
not the *claim protocol*: `engine/query_engine.py`/`engine/messages.py`
(`has_pending_continuation` = the crash-resume entry point that detects an interrupted run;
`sanitize_conversation_messages` = trims a dangling `tool_use` so a resumed transcript is API-valid —
this is the transcript-level analogue of `restart`'s "discard the partial turn"),
`services/session_storage.py` (session persistence/resume), `swarm/worktree.py`
(`create_worktree` fast-resume: if the worktree dir already exists and is a valid git worktree it is
reused rather than recreated — the mechanism a `resume`/`restart` decision drives), `autopilot/
service.py` (`git checkout -B {head} origin/{base}` branch reconciliation as the repo-side reset
primitive). Used to name the resume/reset mechanics concretely; the claim/lease/liveness machinery is
new substance from the conceptual sources, which OpenHarness does not provide.

---

## Why this matters

Specs `#02` and `#07` assume a benign world: a scheduler allocates a `task-id`, hands it to one
runner, and that runner is the only process that ever touches it. `#02` even states "tasks coordinate
only through committed state" and "concurrency is per-worktree." That is correct for *isolation* but
silent on *coordination*. The moment more than one runner can pull work from the shared exec-plan
board (`#07`) — cron jobs, operator-initiated work, and flat specialist lanes all drawing from the
same `active/` directory — two questions appear that nothing else answers:

1. **Who owns this task right now, and is that owner still alive?**
2. **A runner died mid-task. Do we resume it, restart its last turn, or give up?**

The conceptual spec (new-spec 13) converged five inspiration sources onto one anatomy for (1): a
*durable ownership claim* plus an *ephemeral liveness token*, reconciled when they disagree. And it
makes one honest bend to the founding bet: **git is the system of record, but git is a poor
concurrency primitive** — you cannot cheaply compare-and-swap on a ref across processes, and commit
conflicts are a human-resolved mess. So it splits *what the harness persists* (git: low write rate,
auditable) from *how it coordinates* (a fast CAS store: high write rate, ephemeral). That split is
the single new idea; everything else is mechanism.

This is also the spec where **OpenHarness is genuinely thin as a reference, and the spec says so.**
OpenHarness runs one driver per worktree; it has crash-resume *within* a single owner
(`has_pending_continuation` + `sanitize_conversation_messages` repair an interrupted transcript) and
worktree fast-resume, but it has no cross-runner claim, no lease, no heartbeat, no liveness
classification. So the claim/lease/liveness machinery here is carried forward from the conceptual
sources (paperclip/hermes/openclaw/nanobot/voyager), and OpenHarness grounds only the *recovery
seam*: its transcript-repair is precisely the transcript-level analogue of this spec's `restart`
("discard the partial turn, keep the last complete checkpoint"), and its `create_worktree`
fast-resume is the mechanism a `resume`/`restart` decision actually drives. Naming that boundary
honestly is part of the spec — it shows what is reused versus what is net-new.

For (2), paperclip's principle is adopted directly: **recovery is a typed object.** A crash is a
first-class state with a first-class question, answered by reading the turn flight recorder (`#03`)
and the last checkpoint (`#02`). The three invariants stay load-bearing: productive work continues,
only real blockers stop the agent, no infinite loops.

## Scope

**In:** the two-lock claim protocol; the coordination store (the one shared mutable store) and its
schema; claim leases + heartbeat renewal; the 409-never-retry rule; reconciliation
(runtime-owned-first, durable-history-second, grace window); the typed recovery object and its
decision rule; auto-block after repeated claim failures; the liveness tri-state and phase-specific
timeouts; kill-backoff; hard-reset-preserving-state on panic; the durable git mirror at claim
boundaries; per-session serialisation + global concurrency cap.

**Out:** what a turn does internally (→ `#03`); checkpoint refs and worktree teardown (→ `#02`, reused
as-is); the task ledger format and cron kinds (→ `#07`); model/provider failover (→ `#02`/`#11` —
orthogonal: that handles *provider* death, this handles *runner* death); multi-host distribution
(deferred — single-host in v1, per `#02`); the autopilot pipeline that schedules the work (→ `#11`).

## Key decisions (assumed defaults)

1. **Two locks per claim.**
   - `checkout_run_id` — **ownership**. Minted when a runner wins a claim. Durable.
   - `execution_run_id` — **liveness**. Minted at session start inside the worktree,
     heartbeat-renewed. Ephemeral.
   They are separate so "I own this" and "I am alive right now" can diverge — and that divergence is
   the recovery trigger.

2. **One shared coordination store: `.harness/coordination/board.sqlite` (WAL mode).** The *only*
   sanctioned cross-worktree mutable state, the explicit narrow exception to `#02`'s
   no-worktree-to-worktree rule. Holds claim rows + liveness heartbeats. Git-ignored. CAS/read only —
   never a general message bus.

3. **Durable ownership also lands in git.** On claim grant and on release/handoff, the runner writes
   the ownership facts into the task ledger (`#07`) field `claim` and commits. Low write rate (claim,
   release, reclaim only — never per heartbeat), so git never absorbs heartbeat traffic.

4. **Claim lease = `heartbeat_interval × miss_tolerance`.** Defaults: `heartbeat_interval` 15s,
   `miss_tolerance` 60 → **15-minute lease**. A claim is respected while its lease is unexpired; each
   heartbeat renews it. Long lease tolerates GC pauses and long tool calls; short heartbeat gives
   fast crash detection.

5. **409-never-retry.** A runner that tries to claim a task whose lease is *unexpired* and owned by
   someone else receives a denial and **must not retry that task** — it picks the next available task
   from the board. (paperclip)

6. **Reconciliation order is fixed: runtime-owned-first, durable-history-second.** Trust a fresh
   heartbeat over any on-disk record. Only when the lease has expired consult durable history, and
   only after a **grace window** (default `2 × lease` = 30 min beyond the last durable evidence)
   declare the prior holder `lost` and reclaim. (openclaw, paperclip)

7. **Recovery is a typed object**, persisted before any reclaim acts:
   `{ kind: resume | restart | abandon, task_id, prior_checkout_run_id, reason,
   last_good_checkpoint, decided_at }`.

8. **Recovery decision rule** (read the turn flight recorder `#03` + last checkpoint `#02`):
   - `resume` — last checkpoint ends a *complete* turn → spin up a new worktree from it
     (`#02` resume), continue.
   - `restart` — a checkpoint exists but the turn trace shows the turn died *mid-flight* (e.g. last
     state `RUN`, no `SAVE`) → reset to the last complete checkpoint, discard the partial turn.
     (voyager hard-reset-preserving-state; transcript-level analogue: OpenHarness
     `sanitize_conversation_messages`.)
   - `abandon` — no usable checkpoint, or the per-task recovery cap is hit → mark the task `blocked`
     (`#07`), file a tech-debt entry (`#07`), escalate. Never silently drop.

9. **Auto-block after repeated claim failures.** A task reclaimed-then-crashed `max_claim_failures`
   times (default 5) is moved to `blocked` and escalated rather than reclaimed again — the "no
   infinite loops" invariant at the task level. (hermes)

10. **Liveness is a tri-state, not a boolean** (openclaw):
    - `long_running` — heartbeat fresh **and** a progress signal within `stall_threshold`.
    - `stalled` — heartbeat fresh **but** no progress signal for `stall_threshold` (default 5 min).
    - `stuck` — the board says running **but** the heartbeat is stale beyond the lease.
    A "progress signal" = a turn completed, a checkpoint written (`#02`), or a tool result recorded
    (`#03`).

11. **Backoff before killing.** A `stalled` run is not aborted until `5 × stall_threshold`, so a
    merely-slow run is never murdered. Repeated `stuck` diagnostics on an unchanged session back off
    geometrically.

12. **Phase-specific timeouts.** The runner records *which phase* a stall occurred in (`setup`,
    `context-engine`, `first-model-call`, …), capped independently of the overall session timeout, so
    cold-start/auth issues surface in seconds, not after the full budget. (openclaw)

13. **Per-session serialisation + global concurrency cap.** At most one execution per `task-id` at a
    time (the claim enforces it); a global `max_concurrent_runs` semaphore bounds org-wide
    parallelism. (nanobot)

## Artefact shapes

### Coordination store schema (`.harness/coordination/board.sqlite`, WAL)

Table `claims`:
- `task_id` TEXT PRIMARY KEY
- `checkout_run_id` TEXT — current ownership token (null = unclaimed)
- `execution_run_id` TEXT — current liveness token (null = claimed but not yet executing)
- `claimed_by` TEXT — runner/host identifier
- `claimed_at` INTEGER — epoch ms
- `lease_expires_at` INTEGER — epoch ms; renewed on heartbeat
- `last_heartbeat_at` INTEGER — epoch ms
- `last_progress_at` INTEGER — epoch ms; bumped on turn/checkpoint/tool-result
- `last_phase` TEXT — phase label for phase-specific timeout reporting
- `claim_failures` INTEGER — incremented on each crash-then-reclaim
- `state` TEXT — `claimed | executing | releasing | lost | blocked`

All transitions use SQLite `BEGIN IMMEDIATE` (write CAS); the durable mirror in git is written
*after* the CAS commits, never instead of it.

### Durable ownership mirror (ledger `{task-id}.json`, `#07`)

```json
"claim": {
  "checkout_run_id": "co_01H...",
  "claimed_by": "lane-backend@host-3",
  "claimed_at": "2026-06-03T04:20:00Z",
  "released_at": null,
  "recovery_count": 0
}
```
Written on grant, release, and reclaim only. `recovery_count` feeds decisions #8/#9.

### Recovery object (`docs/exec-plans/active/{task-id}.recovery.json`, appended to a list)

```json
{
  "kind": "restart",
  "task_id": "T1",
  "prior_checkout_run_id": "co_01H...",
  "reason": "lease expired 31m ago; turn trace ended at state RUN with no SAVE",
  "last_good_checkpoint": "refs/harness/checkpoints/T1/3",
  "decided_at": "2026-06-03T04:55:00Z"
}
```
Committed to git *before* the reclaim acts, so the audit trail records intent.

## Behaviours

### Claiming a task

1. Runner selects a candidate `task_id` from the board (unclaimed, or lease-expired).
2. Runner opens `BEGIN IMMEDIATE` on `board.sqlite`, re-reads the row.
3. If a live owner exists (lease unexpired, different `checkout_run_id`) → **409**: rollback, pick a
   different task (never retry this one — decision #5).
4. Otherwise mint a fresh `checkout_run_id`, set `state=claimed`, `claimed_by`, `claimed_at`,
   `lease_expires_at = now + lease`, commit the CAS.
5. Mirror ownership into the ledger (`#07`) and commit to git.
6. Proceed to `#02` task-start (worktree + sidecars), then session start (`#03`).

### Heartbeat (during execution)

1. At session start, mint `execution_run_id`, set `state=executing` via CAS.
2. Every `heartbeat_interval`, CAS-update `last_heartbeat_at`, `lease_expires_at = now + lease`, and
   `last_phase`.
3. On each turn end / checkpoint / tool result, bump `last_progress_at`.
4. Heartbeat is fire-and-forget; a failed heartbeat write is logged but does not abort the turn (the
   lease simply ages until the next successful beat).

### Reconciliation (a runner considering an apparently-claimed task)

1. Read the row. **Runtime-owned-first**: if `lease_expires_at > now`, the owner is alive → 409, move
   on (decision #6).
2. If lease expired → **durable-history-second**: read the last checkpoint (`#02`) and ledger
   `updated_at` (`#07`). Compute `idle = now − max(last_progress_at, last checkpoint time)`.
3. If `idle < grace_window` → wait/skip (owner may be mid-GC-pause); do not reclaim yet.
4. If `idle ≥ grace_window` → declare the prior holder `lost`, increment `claim_failures`, and
   produce a recovery object.

### Producing & acting on a recovery object

1. Read turn flight recorder (`#03` `StateTraceEntry`) for the dead run + last checkpoint.
2. Apply decision rule #8 to pick `resume | restart | abandon`.
3. If `claim_failures ≥ max_claim_failures` (decision #9) → force `abandon`.
4. Commit the recovery object to git (intent before action).
5. Execute: `resume`/`restart` → `#02` resume from `last_good_checkpoint` into a new worktree
   (`create_worktree` fast-resume) with a new `checkout_run_id`; for `restart`, the resumed transcript
   is repaired (`sanitize_conversation_messages`) so the discarded partial turn leaves no dangling
   `tool_use`. `abandon` → set ledger task `blocked`, file tech-debt entry (`#07`), fire escalation
   hook (`#13`).

### Liveness classification (monitor / dashboard)

1. Periodically scan `claims` where `state=executing`.
2. Classify per decision #10 (`long_running | stalled | stuck`).
3. `stalled` past `5 × stall_threshold` → abort-drain the run (turn-timeout path, `#03`) and route to
   reconciliation. `stuck` → route to reconciliation immediately; back off repeat diagnostics if the
   row is unchanged.
4. Emit `liveness.classified` on the hook bus (`#13`) with `task_id`, state, and `last_phase`.

### Clean release

1. On task end (success/failure, `#02`), CAS `state=releasing` then clear `checkout_run_id`/
   `execution_run_id`, set the ledger `claim.released_at`, commit git.
2. Crash before release is exactly what reconciliation handles — release is best-effort.

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
12. **MUST** bump `last_progress_at` on turn end, checkpoint write, and tool result.

### Reconciliation (MUST)

13. **MUST** apply runtime-owned-first: a fresh lease wins over any durable record.
14. **MUST** apply durable-history-second only after the lease expires.
15. **MUST** wait out a `grace_window` after the last durable evidence before declaring `lost`.
16. **MUST** increment `claim_failures` on each reclaim.

### Typed recovery (MUST)

17. **MUST** produce a typed recovery object and commit it to git *before* reclaiming.
18. **MUST** choose `resume` only when the last checkpoint ends a complete turn.
19. **MUST** choose `restart` when the turn trace shows a mid-flight death, discarding the partial
    turn while preserving the last complete checkpoint (and repairing the resumed transcript so no
    dangling `tool_use` remains).
20. **MUST** force `abandon` when `claim_failures ≥ max_claim_failures` or no usable checkpoint
    exists, and file a tech-debt entry + escalation rather than silently drop.

### Liveness (MUST/SHOULD)

21. **MUST** classify executing runs into `long_running | stalled | stuck` per the progress-signal +
    lease rules.
22. **MUST NOT** abort a `stalled` run before `5 × stall_threshold`.
23. **MUST** record `last_phase` so a stall is attributable to a phase, not just a duration.
24. **SHOULD** back off repeated `stuck` diagnostics on an unchanged session.
25. **MUST** emit `liveness.classified` on the hook bus (`#13`).

## Acceptance scenarios

```gherkin
Scenario: Live owner blocks a second claimant (409, no retry)
  Given task T1 is claimed by checkout co_A with an unexpired lease
  When runner B attempts to claim T1
  Then runner B receives a 409 denial
  And runner B does not retry T1 in the same pass
  And runner B proceeds to select a different task.

Scenario: Ownership and liveness diverge after a crash
  Given task T1 is claimed by co_A and executing as ex_A
  And runner A's process dies without releasing
  When T1's lease expires and the grace window elapses with no progress
  Then reconciliation declares co_A lost
  And claim_failures for T1 is incremented.

Scenario: Heartbeat renews the lease
  Given task T1 is executing with lease expiring in 15 minutes
  When the runner heartbeats every 15 seconds
  Then lease_expires_at advances on each heartbeat
  And T1 is never eligible for reclaim while the runner is alive.

Scenario: Recovery decides resume on a clean turn boundary
  Given T1 died with its last checkpoint at the end of turn 3
  And the turn-3 trace shows state SAVE completed
  When recovery runs
  Then it writes a recovery object with kind "resume"
  And a new worktree is created from checkpoints/T1/3 with a new checkout_run_id.

Scenario: Recovery decides restart on a mid-flight death
  Given T1's turn-4 trace ends at state RUN with no SAVE
  And the last complete checkpoint is turn 3
  When recovery runs
  Then it writes a recovery object with kind "restart"
  And the partial turn 4 is discarded
  And the resumed transcript has no dangling tool_use
  And execution resumes from checkpoints/T1/3.

Scenario: Recovery abandons after repeated failures
  Given T1 has claim_failures equal to max_claim_failures (5)
  When reconciliation considers T1 again
  Then recovery kind is "abandon"
  And T1's ledger state becomes blocked
  And a tech-debt entry is filed and an escalation hook fires.

Scenario: Liveness tri-state distinguishes slow from stuck
  Given T1 is executing
  When the heartbeat is fresh but no progress signal occurs for stall_threshold
  Then T1 is classified stalled
  And T1 is not aborted until 5x stall_threshold elapses
  When instead the heartbeat goes stale beyond the lease
  Then T1 is classified stuck and routed to reconciliation.

Scenario: Phase-specific timeout surfaces a cold-start stall
  Given T1 stalls before its first model call with last_phase "context-engine"
  When the monitor classifies it
  Then the emitted liveness.classified event names the phase
  And the phase timeout fires independently of the overall session budget.

Scenario: Recovery intent is auditable in git
  Given reconciliation decides to reclaim T1
  When it acts
  Then a recovery object was committed to git before the reclaim
  And git log shows the recovery decision and reason.

Scenario: Durable mirror written only at claim boundaries
  Given T1 runs for an hour heartbeating every 15s
  When the runner completes T1
  Then the ledger claim field was committed on grant and on release only
  And no per-heartbeat commits exist in git history.
```

## Tests

- `test_distinct_ownership_and_liveness_tokens` — two locks minted at different points.
- `test_claim_is_cas_transaction` — concurrent claimants, exactly one wins.
- `test_live_owner_denies_second_claim_409` — lease-respecting denial.
- `test_denied_claim_not_retried_same_pass` — 409-never-retry.
- `test_one_executing_run_per_task` — single-assignee invariant.
- `test_global_semaphore_bounds_concurrency` — org-wide cap.
- `test_heartbeat_renews_lease` — lease advances on beat.
- `test_lease_expiry_is_wallclock` — strict expiry.
- `test_transient_heartbeat_failure_does_not_abort_turn` — resilience.
- `test_progress_signal_bumped_on_turn_checkpoint_toolresult` — progress tracking.
- `test_runtime_owned_first_beats_durable_record` — reconciliation order.
- `test_durable_history_consulted_only_after_lease_expiry` — order.
- `test_grace_window_before_declaring_lost` — no premature reclaim.
- `test_claim_failures_incremented_on_reclaim` — counter.
- `test_recovery_object_committed_before_reclaim` — intent-before-action.
- `test_recovery_resume_on_clean_turn_boundary` — resume rule.
- `test_recovery_restart_on_midflight_death` — restart rule + partial discard.
- `test_restart_resumed_transcript_has_no_dangling_tool_use` — sanitize seam.
- `test_recovery_abandon_at_failure_cap` — abandon + block + escalate.
- `test_recovery_abandon_with_no_usable_checkpoint` — abandon path.
- `test_liveness_long_running_when_progress_fresh` — classification.
- `test_liveness_stalled_when_heartbeat_fresh_no_progress` — classification.
- `test_liveness_stuck_when_heartbeat_stale` — classification.
- `test_stalled_not_aborted_before_5x_threshold` — backoff.
- `test_stuck_diagnostics_back_off_on_unchanged_session` — anti-spam.
- `test_last_phase_recorded_for_stall` — phase attribution.
- `test_liveness_classified_hook_emitted` — hook integration (#13).
- `test_board_sqlite_is_git_ignored` — coordination store hygiene.
- `test_durable_mirror_written_only_at_claim_boundaries` — no per-heartbeat commits.
- `test_clean_release_clears_both_tokens` — release path.

## Edge cases

- **Clock skew between hosts.** v1 is single-host (`#02`), so wall-clock leases are safe. Multi-host
  must move to a monotonic/lease-server scheme — flagged, deferred.
- **`board.sqlite` corrupted or deleted.** Rebuildable from the durable ledger mirrors at startup:
  scan `active/*.json` `claim` fields, treat any with no live process as expired. The git mirror is
  the source of truth *of record*; the board is the source of truth *of now*.
- **A runner heartbeats but makes no progress forever (live-but-useless).** Caught by the `stalled`
  classification, not by the lease — exactly why liveness is a tri-state and not "lease fresh =
  healthy."
- **Grace window swallows a genuinely-dead-but-recently-checkpointed run.** Acceptable: a late reclaim
  is preferred over a double-execution. The grace window is the explicit knob.
- **Recovery chooses `restart` but the "partial" turn already pushed a side effect** (e.g. an external
  API call). Side effects outside the worktree are `#08`-the-sandbox's problem (sandbox tiers); inside
  the worktree, the checkpoint reset is clean. Cross-boundary idempotency is an open question.
- **Two runners both observe an expired lease simultaneously and both try to reclaim.** The reclaim is
  itself a CAS (mint new `checkout_run_id` under `BEGIN IMMEDIATE`); exactly one wins, the other gets
  a 409 on the fresh claim and moves on.
- **`max_concurrent_runs` reached while a high-priority task waits.** v1: FIFO by board selection
  order; priority queuing is deferred (consistent with `#07`'s deferred priority).

## Open questions

- Whether the coordination store should be SQLite-WAL (simple, single-host) or a small lease server
  from day one (needed for multi-host, `#02`'s deferred direction).
- Whether `restart` should attempt to *compensate* external side effects of the discarded turn, or
  always assume sandbox tiers (`#08`-the-sandbox) made them safe to repeat.
- Whether the grace window should be fixed (`2 × lease`) or adaptive to the task's observed turn
  duration.
- Whether liveness classification belongs in a dedicated monitor process or piggybacks on the next
  runner's board scan (current assumption: any runner can classify).
- Whether `claim_failures` should decay over time (a task that failed 4 times last month but is
  healthy now) or stay monotonic until manual reset.

## Out of scope

- Turn-internal FSM and the `StateTraceEntry` shape (→ `#03`; consumed here, not defined here).
- Checkpoint refs, worktree creation/teardown, atomic writes (→ `#02`; reused as-is).
- Task ledger format, plan states, cron kinds (→ `#07`; this spec adds only the `claim` field).
- Model/provider failover and cooldowns (→ `#02`/`#11`; orthogonal — provider death vs runner death).
- Multi-host claim distribution and cross-host clock authority (deferred — single-host v1).
- Priority queuing / fair scheduling across waiting tasks (deferred, per `#07`).
- The autopilot pipeline that schedules and promotes the work (→ `#11`).
