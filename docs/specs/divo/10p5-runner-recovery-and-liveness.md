# 10p5 — Runner Recovery & Liveness

**One-liner:** A claimed task whose owner died is *recovered, not abandoned*: reconciliation
(runtime-owned-first, durable-history-second, with a grace window) decides a claim is `lost`; recovery
is a **typed decision object** (`resume | restart | abandon`) committed to git *before* it acts, not
an exception; and liveness is a **tri-state** (`long_running | stalled | stuck`), never a boolean,
with phase-specific timeouts and kill-backoff. Builds on `#08`'s claim/lease + coordination store;
consumed by `#09` (autopilot crash-recovery) and `#10` (swarm member death).

> **Split note.** This spec was divided from the original `08-claim-recovery-liveness`. `#08` (Task
> Claim & Lease) is the *correctness floor* — who owns a task, is the lease live, who runs
> concurrently. **10p5 (here)** is the *resilience layer* — what happens when a runner dies. It is
> numbered `10p5` because it is built *after* `#10` (swarm): recovery and liveness integration are
> shaped by how a real swarm fails, so building them before a consumer exists repeats the
> no-consumer-defines-the-integration risk. A swarm is *correct* on `#08` alone (a dead owner's lease
> expires and the task is reclaimed-and-restarted); 10p5 makes that reclaim *graceful*.

**Sources (source of truth):** `docs/specs/new-specs/13-claim-recovery-liveness.md` — the
reconciliation order (runtime-owned-first, durable-history-second, grace window), the typed recovery
object + decision rule, auto-block after `max_claim_failures`, the liveness tri-state with
phase-specific timeouts and kill-backoff, and their acceptance criteria/Gherkin/tests are carried
forward verbatim-in-substance here (the claim/lease portions of that conceptual spec live in `#08`).
That spec's recovery/liveness lineage (openclaw liveness tri-state + phase timeouts · voyager
hard-reset-preserving-state · hermes auto-block · paperclip recovery-is-a-typed-object) is the
conceptual authority. · `#08` (the two-lock claim, the `board.sqlite` CAS store, the lease + heartbeat
this spec reconciles and classifies) · `#03` (the turn records / flight recorder read to decide
resume-vs-restart; transcript repair on `restart`) · `#02` (checkpoint refs + `resume_from`
fast-resume that a `resume`/`restart` decision drives) · `#07` (the ledger `claim.recovery_count`;
abandon files a tech-debt entry and sets the task `blocked`) · `#09`/`#10` (the consumers whose
failure modes shape this) · `#13` (the `liveness.classified` and escalation events ride the hook bus).

**Reference (grounding only, not authority):** [openharness] OpenHarness runs **one driver per
worktree**; it has no cross-runner claim, lease, or heartbeat (those are `#08`). It *does* ground the
**recovery seam** this spec needs: `engine/query_engine.py`/`engine/messages.py`
(`has_pending_continuation` = the crash-resume entry point that detects an interrupted run;
`sanitize_conversation_messages` = trims a dangling `tool_use` so a resumed transcript is API-valid —
the transcript-level analogue of `restart`'s "discard the partial turn"), `services/
session_storage.py` (session persistence/resume), `swarm/worktree.py` (`create_worktree` fast-resume),
`autopilot/service.py` (`git checkout -B {head} origin/{base}` branch reconciliation). Used to name
the resume/reset mechanics concretely; the reconciliation/liveness machinery is new substance from the
conceptual sources.

---

## Why this matters

`#08` answers *who owns a task and is the owner alive*. It deliberately stops there: on lease expiry a
task is reclaimable, and at that floor reclaim means "start over." That is *correct* but wasteful and
blunt — it throws away an hour of valid work because a runner died on turn 9, and it cannot tell a
genuinely-dead runner from a merely-slow one.

This spec adds the second question `#08` names but does not answer:

> **A runner died mid-task. Do we resume it, restart its last turn, or give up?**

paperclip's principle is adopted directly: **recovery is a typed object.** A crash is a first-class
state with a first-class question, answered by reading the turn records (`#03`) and the last checkpoint
(`#02`), and the decision is committed to git *before* it acts so the audit trail records intent. And
liveness is openclaw's tri-state, not a boolean: a runner that heartbeats but makes no progress
(`stalled`) is distinct from one whose heartbeat went stale (`stuck`), and a merely-slow run is never
murdered.

dream-specific grounding: dream's turn loop already writes a `TurnRecord` per turn with
`outcome ∈ {complete, timeout, aborted}` (`#03`). So "did the turn finish cleanly?" is answerable from
the existing record — this spec reads `TurnRecord.outcome` + checkpoint correlation rather than
inventing a separate intra-turn flight recorder. (Where the conceptual spec assumed a `StateTraceEntry`
with explicit `RUN`/`SAVE` states, dream's `TurnRecord.outcome` is the equivalent signal.)

The three invariants (paperclip) stay load-bearing: productive work continues (recover instead of
discard), only real blockers stop the agent (abandon escalates, never silently drops), no infinite
loops (`max_claim_failures`).

## Scope

**In:** reconciliation order (runtime-owned-first, durable-history-second) + the grace window; the
typed recovery object and its decision rule (read `#03` turn records + `#02` checkpoint); auto-block
after `max_claim_failures`; the liveness tri-state and phase-specific timeouts; kill-backoff;
hard-reset-preserving-state on panic; the `board.sqlite` schema extension
(`last_progress_at`/`last_phase`/`claim_failures`, the `lost`/`blocked` states) and the
progress-signal bumps that feed it; the `liveness.classified` + escalation events.

**Out:** the two-lock claim protocol, the coordination store + base schema, the lease + heartbeat
itself, 409-never-retry, and the concurrency cap (→ `#08`, consumed here); what a turn does internally
and the `TurnRecord` shape (→ `#03`); checkpoint refs and worktree teardown (→ `#02`, reused as-is);
the task ledger format (→ `#07`); model/provider failover (→ `#02`/`#11` — provider death vs runner
death); multi-host distribution (deferred); the loops that schedule the work (→ `#09`, `#10`).

## Key decisions (assumed defaults)

1. **Reconciliation order is fixed: runtime-owned-first, durable-history-second.** Trust a fresh
   heartbeat (`#08` lease) over any on-disk record. Only when the lease has expired consult durable
   history, and only after a **grace window** (default `2 × lease` = 30 min beyond the last durable
   evidence) declare the prior holder `lost` and reclaim. (openclaw, paperclip)

2. **Recovery is a typed object**, persisted before any reclaim acts:
   `{ kind: resume | restart | abandon, task_id, prior_checkout_run_id, reason,
   last_good_checkpoint, decided_at }`.

3. **Recovery decision rule** (read the last `TurnRecord` `#03` + last checkpoint `#02`):
   - `resume` — the last checkpoint correlates to a `TurnRecord` with `outcome="complete"` (a clean
     turn boundary) → spin up a new worktree from it (`#02` `resume_from`), continue.
   - `restart` — a checkpoint exists but the latest turn died mid-flight (no `complete` `TurnRecord`
     for it, or `outcome ∈ {timeout, aborted}`) → reset to the last complete checkpoint, discard the
     partial turn. (voyager hard-reset-preserving-state; transcript-level analogue: OpenHarness
     `sanitize_conversation_messages` repairs any dangling `tool_use`.)
   - `abandon` — no usable checkpoint, or the per-task recovery cap is hit → mark the task `blocked`
     (`#07`), file a tech-debt entry (`#07`), escalate. Never silently drop.

4. **Auto-block after repeated claim failures.** A task reclaimed-then-crashed `max_claim_failures`
   times (default 5) is moved to `blocked` and escalated rather than reclaimed again — the "no
   infinite loops" invariant at the task level. (hermes)

5. **Liveness is a tri-state, not a boolean** (openclaw):
   - `long_running` — heartbeat fresh **and** a progress signal within `stall_threshold`.
   - `stalled` — heartbeat fresh **but** no progress signal for `stall_threshold` (default 5 min).
   - `stuck` — the board says running **but** the heartbeat is stale beyond the lease.
   A "progress signal" = a turn completed, a checkpoint written (`#02`), or a tool result recorded
   (`#03`).

6. **Backoff before killing.** A `stalled` run is not aborted until `5 × stall_threshold`, so a
   merely-slow run is never murdered. Repeated `stuck` diagnostics on an unchanged session back off
   geometrically.

7. **Phase-specific timeouts.** The runner records *which phase* a stall occurred in (`setup`,
   `context-engine`, `first-model-call`, …) in `last_phase`, capped independently of the overall
   session timeout, so cold-start/auth issues surface in seconds, not after the full budget. (openclaw)

8. **The decision reads `TurnRecord.outcome`, not a new flight recorder.** dream already writes
   `TurnRecord{outcome}` per turn (`#03`); this spec consumes it for the resume-vs-restart split rather
   than adding an intra-turn `StateTraceEntry`. If `#03`'s record shape later gains finer state, the
   decision rule can sharpen, but it MUST NOT require it.

## Artefact shapes

### Coordination store schema extension (`board.sqlite`, extends `#08`'s `claims`)

Added columns/states:
- `last_progress_at` INTEGER — epoch ms; bumped on turn end / checkpoint / tool result.
- `last_phase` TEXT — phase label for phase-specific timeout reporting.
- `claim_failures` INTEGER — incremented on each crash-then-reclaim.
- `state` gains `lost | blocked` beyond `#08`'s `claimed | executing | releasing`.

### Durable mirror extension (ledger `{task-id}.json`, `#07`)

`#08`'s `claim` object gains `recovery_count` (feeds decisions #3/#4):
```json
"claim": { "checkout_run_id": "co_01H...", "claimed_by": "...", "claimed_at": "...",
           "released_at": null, "recovery_count": 0 }
```

### Recovery object (`docs/exec-plans/active/{task-id}.recovery.json`, appended to a list)

```json
{
  "kind": "restart",
  "task_id": "T1",
  "prior_checkout_run_id": "co_01H...",
  "reason": "lease expired 31m ago; last turn TurnRecord outcome=aborted, no complete turn 4",
  "last_good_checkpoint": "refs/dream/checkpoints/T1/3",
  "decided_at": "2026-06-03T04:55:00Z"
}
```
Committed to git *before* the reclaim acts, so the audit trail records intent.

## Behaviours

### Reconciliation (a runner considering an apparently-claimed task)

1. Read the row. **Runtime-owned-first**: if `lease_expires_at > now` (`#08` lease), the owner is alive
   → 409, move on.
2. If lease expired → **durable-history-second**: read the last checkpoint (`#02`) and ledger
   `updated_at` (`#07`). Compute `idle = now − max(last_progress_at, last checkpoint time)`.
3. If `idle < grace_window` → wait/skip (owner may be mid-GC-pause); do not reclaim yet.
4. If `idle ≥ grace_window` → declare the prior holder `lost`, increment `claim_failures`, and produce
   a recovery object.

### Producing & acting on a recovery object

1. Read the dead run's last `TurnRecord` (`#03`) + last checkpoint (`#02`).
2. Apply decision rule #3 to pick `resume | restart | abandon`.
3. If `claim_failures ≥ max_claim_failures` (decision #4) → force `abandon`.
4. Commit the recovery object to git (intent before action).
5. Execute: `resume`/`restart` → `#02` `resume_from(last_good_checkpoint)` into a new worktree with a
   fresh `#08` `checkout_run_id`; for `restart`, repair the resumed transcript
   (`sanitize_conversation_messages`) so the discarded partial turn leaves no dangling `tool_use`.
   `abandon` → set ledger task `blocked`, file tech-debt entry (`#07`), fire escalation hook (`#13`).

### Heartbeat progress signals (extends `#08` heartbeat)

1. On each turn end / checkpoint write / tool result, bump `last_progress_at` and set `last_phase` via
   CAS. (`#08`'s heartbeat already renews the lease; this adds the progress + phase columns.)

### Liveness classification (monitor / dashboard)

1. Periodically scan `claims` where `state=executing`.
2. Classify per decision #5 (`long_running | stalled | stuck`).
3. `stalled` past `5 × stall_threshold` → abort-drain the run (turn-timeout path, `#03`) and route to
   reconciliation. `stuck` → route to reconciliation immediately; back off repeat diagnostics if the
   row is unchanged.
4. Emit `liveness.classified` on the hook bus (`#13`) with `task_id`, state, and `last_phase`.

## Acceptance criteria

### Reconciliation (MUST)

1. **MUST** apply runtime-owned-first: a fresh lease wins over any durable record.
2. **MUST** apply durable-history-second only after the lease expires.
3. **MUST** wait out a `grace_window` after the last durable evidence before declaring `lost`.
4. **MUST** increment `claim_failures` on each reclaim.

### Typed recovery (MUST)

5. **MUST** produce a typed recovery object and commit it to git *before* reclaiming.
6. **MUST** choose `resume` only when the last checkpoint correlates to a `complete` `TurnRecord`.
7. **MUST** choose `restart` when the latest turn died mid-flight, discarding the partial turn while
   preserving the last complete checkpoint (and repairing the resumed transcript so no dangling
   `tool_use` remains).
8. **MUST** force `abandon` when `claim_failures ≥ max_claim_failures` or no usable checkpoint exists,
   and file a tech-debt entry + escalation rather than silently drop.

### Progress & liveness (MUST/SHOULD)

9. **MUST** bump `last_progress_at` on turn end, checkpoint write, and tool result.
10. **MUST** classify executing runs into `long_running | stalled | stuck` per the progress-signal +
    lease rules.
11. **MUST NOT** abort a `stalled` run before `5 × stall_threshold`.
12. **MUST** record `last_phase` so a stall is attributable to a phase, not just a duration.
13. **SHOULD** back off repeated `stuck` diagnostics on an unchanged session.
14. **MUST** emit `liveness.classified` on the hook bus (`#13`).

## Acceptance scenarios

```gherkin
Scenario: Ownership and liveness diverge after a crash
  Given task T1 is claimed by co_A and executing as ex_A
  And runner A's process dies without releasing
  When T1's lease expires and the grace window elapses with no progress
  Then reconciliation declares co_A lost
  And claim_failures for T1 is incremented.

Scenario: Runtime-owned-first beats a stale durable record
  Given the ledger shows an old claim but the board lease is fresh
  When a runner reconciles T1
  Then it respects the live lease and does not reclaim.

Scenario: Grace window prevents a premature reclaim
  Given T1's lease expired 5 minutes ago and grace_window is 30 minutes
  When a runner considers T1
  Then it waits rather than reclaiming.

Scenario: Recovery decides resume on a clean turn boundary
  Given T1 died with its last checkpoint at the end of turn 3
  And turn 3's TurnRecord outcome is complete
  When recovery runs
  Then it writes a recovery object with kind "resume"
  And a new worktree is created from checkpoints/T1/3 with a fresh checkout_run_id.

Scenario: Recovery decides restart on a mid-flight death
  Given T1's turn 4 has no complete TurnRecord (outcome aborted)
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
```

## Tests

- `test_runtime_owned_first_beats_durable_record` — reconciliation order.
- `test_durable_history_consulted_only_after_lease_expiry` — order.
- `test_grace_window_before_declaring_lost` — no premature reclaim.
- `test_claim_failures_incremented_on_reclaim` — counter.
- `test_recovery_object_committed_before_reclaim` — intent-before-action.
- `test_recovery_resume_on_clean_turn_boundary` — resume rule (complete TurnRecord).
- `test_recovery_restart_on_midflight_death` — restart rule + partial discard.
- `test_restart_resumed_transcript_has_no_dangling_tool_use` — sanitize seam.
- `test_recovery_abandon_at_failure_cap` — abandon + block + escalate.
- `test_recovery_abandon_with_no_usable_checkpoint` — abandon path.
- `test_progress_signal_bumped_on_turn_checkpoint_toolresult` — progress tracking.
- `test_liveness_long_running_when_progress_fresh` — classification.
- `test_liveness_stalled_when_heartbeat_fresh_no_progress` — classification.
- `test_liveness_stuck_when_heartbeat_stale` — classification.
- `test_stalled_not_aborted_before_5x_threshold` — backoff (injected clock).
- `test_stuck_diagnostics_back_off_on_unchanged_session` — anti-spam.
- `test_last_phase_recorded_for_stall` — phase attribution.
- `test_liveness_classified_hook_emitted` — hook integration (`#13`).

## Edge cases

- **Grace window swallows a genuinely-dead-but-recently-checkpointed run.** Acceptable: a late reclaim
  is preferred over a double-execution. The grace window is the explicit knob.
- **Recovery chooses `restart` but the "partial" turn already pushed a side effect** (e.g. an external
  API call). Side effects outside the worktree are `#13`-the-sandbox's problem (sandbox tiers); inside
  the worktree the checkpoint reset is clean. Cross-boundary idempotency is an open question.
- **A runner heartbeats but makes no progress forever (live-but-useless).** Caught by the `stalled`
  classification, not by the `#08` lease — exactly why liveness is a tri-state.
- **Two runners both decide to reclaim the same expired lease.** The reclaim itself is `#08`'s CAS;
  exactly one wins. Each may independently produce a recovery object, but only the CAS winner acts;
  the loser's object is harmless audit (or skipped once it sees the fresh claim).
- **`claim_failures` on a task that failed last month but is healthy now.** Open question: decay vs
  monotonic-until-manual-reset.

## Open questions

- Whether `restart` should attempt to *compensate* external side effects of the discarded turn, or
  always assume sandbox tiers (`#13`) made them safe to repeat.
- Whether the grace window should be fixed (`2 × lease`) or adaptive to the task's observed turn
  duration.
- Whether liveness classification belongs in a dedicated monitor process or piggybacks on the next
  runner's board scan (current assumption: any runner can classify).
- Whether `claim_failures` should decay over time or stay monotonic until manual reset.
- Whether `#03` should eventually emit a finer intra-turn state trace so the resume/restart decision
  can sharpen beyond `TurnRecord.outcome`.

## Out of scope

- The two-lock claim, the `board.sqlite` base schema, the lease + heartbeat, 409-never-retry, and the
  concurrency cap (→ `#08`).
- Turn-internal FSM and the `TurnRecord` shape (→ `#03`; consumed here, not defined here).
- Checkpoint refs, worktree creation/teardown, atomic writes (→ `#02`; reused as-is).
- Task ledger format, plan states, cron kinds (→ `#07`).
- Model/provider failover and cooldowns (→ `#02`/`#11`).
- Multi-host claim distribution and cross-host clock authority (deferred — single-host v1).
- The autopilot/swarm loops that schedule and run the work (→ `#09`, `#10`).

---

## Parked work tracked here (not part of 10p5)

The following are un-built pieces parked in this doc for visibility. They are
**unrelated to recovery/liveness** and will graduate to their own specs/slices
when built — they're recorded here so the remaining-work surface isn't lost.

### `#12d` — Evaluator (rubric-based sprint verdict)

The separate-context **evaluator** that grades a generator's sprint against a
negotiated rubric and renders `pass | needs-changes | fail`, mapped to the
ledger by `#10` (`pass` → `done`, `needs-changes` → `in_progress`, `fail` →
`blocked` + tech-debt entry). Writes one **evaluation record** per sprint
(`docs/evals/{task-id}/sprint-{n}.json`) — the score + rubric outcome.

- **Why parked:** depends on `#10`'s sprint contract (the thing being graded) and
  the rubric content/weights live here. The `evaluation.record` trace event
  (`#12a`) is already shipped, and `#12c`'s verification report feeds it.
- **Blocking for:** the rolling pass-rate (below) and `#11`'s dream-phase
  consolidation signal — both read evaluation records.

### `#12e` left-over — Rolling pass-rate metric (= left-over [#02](../left-over/02-rolling-pass-rate.md))

Per-axis / per-task / per-session **pass-rate** computed over a window of
evaluation records, surfaced to the `#11` dream phase as a quality signal.

- **Why parked:** blocked on `#12d` (the producer of rubric outcomes) — there is
  no record store to aggregate until the evaluator exists. Already captured as
  left-over **#02**; cross-referenced here.
- **Reuse:** likely the `#12b` `query_metrics` shape over a derived metric once
  `evaluation.record` events carry the outcome.

### `#13` — Hook bus (observer-only extension surface)

A **fire-and-forget, observer-only** hook bus: handlers **never veto**, each
bounded by a **1-second** wall-clock deadline, stable dot-separated
`{subject}.{verb}.{tense}` names, catalogue in `docs/_schemas/hook-catalogue.md`
(14-entry minimum). A raising handler is logged and ignored; it cannot prevent
the lifecycle event.

- **Why parked:** buildable now (no blockers) but the start of a larger
  extension-surface arc; lower priority than the security envelope (`13A–F`).
- **Foundational for:** plugins (below), which subscribe to hooks.

### `#13` — Plugins + slash-command registry (repo-local, capability-gated)

Repo-local plugins under `plugins/{name}/` with a `manifest.toml`
(name/version/entry/hook-subscriptions/slash-commands/required-capabilities) and
a `setup(runtime)`/`teardown()` lifecycle. **Opt-in** via
`.harness/plugins-enabled.toml`; **in-process** for v1; **capability-gated** —
the runner refuses to load a plugin whose declared capabilities exceed the
session's sandbox tier (`13A`/`13B`). Plugin failure never aborts the session;
`/reload-plugins` re-runs the load. Plus the flat slash-command registry
(duplicate-refusal, built-ins: `/help`, `/status`, `/reset`, `/replan`,
`/sandbox-tier`, `/reload-plugins`).

- **Why parked:** depends on the hook bus (above) and the `#13` tier model
  (shipped in `13A`/`13B`); the second half of `#13`'s extension surface.
- **Note:** the `permissions/` tier model (`13A`/`13B`) is exactly the gate a
  plugin's declared capabilities are checked against.
