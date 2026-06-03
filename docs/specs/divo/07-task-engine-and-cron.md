# 07 — Task Engine & Cron

**One-liner:** Two task representations that compose — the **durable exec-plan** (versioned Markdown
rationale + JSON ledger in the repo, the system of record for multi-session work) and the
**ephemeral background-task record** (the in-memory runtime handle that actually spawns, supervises,
streams, and reaps a subprocess or session) — wired together by completion listeners, and driven on
a schedule by a **cron registry** where every recurring job runs as a normal harness session in its
own worktree.

**Sources (source of truth):** `docs/specs/new-specs/09-task-engine-and-cron.md` — the exec-plan
format (Markdown narrative + JSON ledger under `docs/exec-plans/`), the ledger entry shape and the
`draft → active → completed → archived` plan FSM, the single rolling `tech-debt-tracker.md`
(append-only, operator-triaged, never auto-acted-on), the four default cron kinds
(`doc-garden`/`quality-grade`/`refactor-deviation`/`reference-refresh`) and their cadence, the
cron-manifest format, the **cron-as-normal-session** execution model, the cron run-record artefact,
operator extensibility, the governance "never auto-merge governance-tier files" rule, and the full
acceptance criteria/Gherkin/tests are carried forward and enriched here. · `#01` (exec-plans,
ledgers, tech-debt, cron manifests, and run-records are all version-controlled repo artefacts;
ledger schema validated at session start) · `#02` (every background/cron task runs in its own
worktree) · `#03` (a cron job is a normal session: same FSM, orientation, sealing; `max_session_minutes`
rides the turn-timeout machinery) · `#08` (each task/cron manifest carries a `tier_required` sandbox
gate) · `#10`/`#13` (governance gate: cron jobs open PRs for governance-tier files, never auto-merge;
`cron.run.complete` fires on the hook bus) · `#11` (autopilot consumes the exec-plan ledger as its
work queue).
**Reference (grounding only, not authority):** [openharness] `tasks/types.py`
(`TaskRecord` = `id`/`type`/`status`/`description`/`cwd`/`output_file`/`command`/`prompt`/
`created_at`/`started_at`/`ended_at`/`return_code`/`metadata`/`env`/`argv`;
`TaskType = local_bash | local_agent | remote_agent | in_process_teammate | dream`;
`TaskStatus = pending | running | completed | failed | killed`), `tasks/manager.py`
(`BackgroundTaskManager.create_shell_task`/`get_task`/`list_tasks(status=)`/`stop_task`/
`register_completion_listener`/`_notify_completion_listeners`, per-task `output_file` streaming,
`_generations` restart counter, `CompletionListener` callback type, the `argv`-vs-`command`
shell-bypass note), `tasks/stop_task.py`, `tasks/local_agent_task.py`/`local_shell_task.py`,
`services/cron.py` (`load_cron_jobs`/`save_cron_jobs` JSON registry, `validate_cron_expression`
(croniter), `validate_timezone` (IANA), `next_run_time` (UTC, tz-aware), `upsert_cron_job`
(enabled-default, `next_run` computed, `exclusive_file_lock`-guarded)), `services/cron_scheduler.py`,
`tools/{task_create,task_get,task_list,task_output,task_stop,task_update}_tool.py`,
`tools/{cron_create,cron_list,cron_delete,cron_toggle}_tool.py` — used to name the runtime task and
cron-registry primitives concretely.

---

## Why this matters

A harness that only runs when an operator types at a terminal is an assistant, not a system. Two
capabilities close the gap from "tool you invoke" to "system that maintains itself":

1. **A task representation that outlives a single session** — pausable, resumable, progress-tracked
   at finer grain than "session done," and inspectable in code review.
2. **A scheduler** that wakes the harness on its own to do the recurring housekeeping nobody enjoys —
   refresh stale docs, regrade quality, scan for drift from core beliefs, re-fetch vendored
   references.

The conceptual spec (new-spec 09) fixes the durable, filesystem-shaped representation: exec-plans are
Markdown + JSON under `docs/exec-plans/`, so they diff in review and survive any crash (invariant #2,
the repo is the system of record). It also fixes the scheduling posture: cron jobs are *just another
session* so the same FSM/validator/observability/sandbox apply, and they never auto-merge governance
files.

What the conceptual spec under-specifies — and what OpenHarness's `tasks/manager.py` supplies — is
the *runtime layer beneath the durable plan*. A plan is a record; something has to actually spawn the
process, stream its output, notice when it finishes, and update the record. That is the
**`BackgroundTaskManager`**: it holds ephemeral `TaskRecord`s (`pending → running →
completed|failed|killed`), each backed by an `output_file` it streams, and it fires **completion
listeners** when a task reaches a terminal state. This gives us the missing wiring and three
enrichments worth promoting:

1. **Two task layers, not one.** The durable **exec-plan** (the *what* and *why*, in the repo) and
   the ephemeral **`TaskRecord`** (the *running process*, in memory + an `output_file`) are distinct
   and composed. The completion listener is the seam: when a background task that was advancing a
   ledger entry finishes, the listener updates the durable ledger and commits it. Conflating the two
   loses either durability (if everything is in-memory) or supervisability (if everything is a file).

2. **A typed task taxonomy.** OpenHarness's `TaskType` (`local_bash`, `local_agent`,
   `remote_agent`, `in_process_teammate`, `dream`) names the kinds of work the runtime supervises —
   a raw shell command, a full agent session, a remote agent, a teammate (`#10`), and the
   self-evolution "dream" run (`#11`). Cron-as-session is concretely "a cron trigger spawns a
   `local_agent` task," which makes the conceptual "cron is a normal session" rule mechanical.

3. **A real cron registry with timezone-correct scheduling.** The conceptual cron manifest is
   enriched by OpenHarness's `services/cron.py`: a file-locked JSON registry, `croniter`-validated
   expressions, IANA-timezone-aware `next_run_time` (always stored UTC), and `upsert_cron_job` that
   computes the next run on write. This is the difference between "we'll wire a scheduler somehow"
   and a concrete, race-safe registry.

## Scope

**In:** the durable exec-plan artefact (Markdown sections + JSON ledger) and its `draft → active →
completed → archived` FSM; ledger entry semantics (single-`in_progress`, `done`⇒`passes`, append-only
`notes`); the rolling append-only `tech-debt-tracker.md`; the ephemeral `TaskRecord` runtime model
(`TaskType`/`TaskStatus`, `output_file` streaming, `argv`-vs-`command`, restart `_generations`); the
`BackgroundTaskManager` lifecycle (`create`/`get`/`list`/`stop`) and **completion listeners** as the
durable↔ephemeral seam; the cron registry (`load`/`save`/`upsert`, croniter validation, tz-aware
`next_run`, file-locked writes); the cron-manifest format and the four default kinds; the
cron-as-session execution model; the cron run-record; operator extensibility for tasks and cron;
the governance no-auto-merge rule; the `task_*`/`cron_*` tool surface.

**Out:** the planner subagent that *authors* exec-plans (→ `#10`); the autopilot pipeline that
*consumes* the ledger as a work queue (→ `#11`); the underlying OS scheduler that fires triggers
(cron/systemd/Task Scheduler — implementation detail); the verification rubric that produces a
`passes` verdict (→ `#12`); the sandbox tier *definitions* (→ `#08`); the hook bus the
`cron.run.complete` event rides (→ `#13`); the streaming session FSM a cron job runs (→ `#03`);
the "dream" self-evolution task body (→ `#11`, this spec only notes `dream` as a `TaskType`).

## Key decisions (assumed defaults)

1. **Two composed task representations.**
   - **Durable exec-plan** = `docs/exec-plans/active/{task-id}.md` (narrative) + `{task-id}.json`
     (ledger). The system of record for multi-session work (`#01`).
   - **Ephemeral `TaskRecord`** = the in-memory `BackgroundTaskManager` handle for one running
     process/session, backed by an `output_file`. Holds runtime state only; the durable truth is the
     ledger.

2. **One Markdown + one JSON per active task.** Narrative sections in the `.md`; machine-readable
   state in the `.json` ledger. Committed together. Ledger schema in
   `docs/_schemas/exec-plan-ledger.schema.json`, validated at session start (`#01`).

3. **Ledger entry shape:** `id`, `description`, `steps` (ordered sub-step strings), `status`
   (`pending | in_progress | done | blocked`), `passes` (bool, from `#12`), `notes` (append-only).

4. **Plan FSM:** `draft → active → completed → archived`. Completed plans move to
   `docs/exec-plans/completed/`; after the retention window (default 90 days) `doc-garden` moves them
   to `archived/`. Plans are **moved, never deleted** (auditability).

5. **Ledger invariants:** at most **one `in_progress` entry per task**; any entry marked `done` by an
   evaluator-enabled task **MUST** have `passes = true`; `notes` are **append-only**.

6. **Tech-debt tracker is a single rolling append-only file** (`docs/exec-plans/tech-debt-tracker.md`).
   Harness writes append `ts`/`source`/`task_id`/`missing`/`evidence`; the harness **never acts on
   entries autonomously** — the operator triages by editing the file.

7. **Runtime task model (`TaskRecord`):** typed by `TaskType` (`local_bash` | `local_agent` |
   `remote_agent` | `in_process_teammate` | `dream`) and `TaskStatus` (`pending` | `running` |
   `completed` | `failed` | `killed`). Each task streams to an `output_file` (read incrementally via
   `task_output`), records `return_code`, and carries a `_generations` counter so a restarted agent
   task is distinguishable from its predecessor.

8. **`argv` over `command` where the shell is a liability.** A task may be launched as a shell string
   (`command`) or a direct argv vector (`argv`); the argv form bypasses the shell entirely — the
   right choice for teammate spawning on platforms where shell quoting of binary paths is unreliable.

9. **Completion listeners are the durable↔ephemeral seam.** `BackgroundTaskManager` fires registered
   `CompletionListener`s when a task reaches a terminal state. The harness registers a listener that:
   updates the exec-plan ledger entry the task was advancing, writes the cron run-record if the task
   was a cron session, and fires the relevant hook (`#13`). This is how a process finishing becomes a
   durable, committed fact.

10. **Cron registry is a file-locked JSON store.** Jobs persist via `upsert_cron_job` under an
    `exclusive_file_lock`; `next_run` is computed on write from a `croniter`-validated `schedule`
    interpreted in the job's IANA `timezone` and **stored as UTC**. Invalid expressions/timezones are
    rejected at write time.

11. **Cron jobs run as normal harness sessions.** A trigger spawns a `local_agent` `TaskRecord` in
    its own worktree (`#02`) with the manifest's `entry_prompt` as the initial intent; it runs the
    standard FSM (`#03`), validator, and observability, at the manifest's `tier_required` (`#08`).

12. **Four default cron kinds shipped:** `doc-garden` (daily — stale-doc PRs + archive-old-plans),
    `quality-grade` (weekly — rubric over recent commits → `docs/QUALITY_SCORE.md`),
    `refactor-deviation` (daily — core-belief violations → small PRs / tech-debt entries),
    `reference-refresh` (weekly — re-fetch vendored `docs/references/`).

13. **Cron kinds are manifest-defined and operator-extensible.** `.harness/cron/{kind}.toml` declares
    a kind; operators add kinds without forking, disable any kind via `enabled = false` (recorded as
    a `cron.skipped` event on the next trigger), and override `schedule`/`tier_required`/
    `max_session_minutes`.

14. **Cron jobs never auto-merge governance-tier files.** A cron job that would touch
    `core-beliefs.md` or `product-specs/` opens a `[governance]`-tagged PR for human merge
    (`#10`/`#13`), never commits to base.

15. **`max_session_minutes` is a hard cron-level cap.** Enforced via the `#03` turn-timeout
    machinery; an overrun aborts the session and records `outcome: failed (max-session-minutes)` in
    the run-record.

## Artefact shapes

### Exec-plan Markdown (`docs/exec-plans/active/{task-id}.md`)

Sections: **Goal**, **Why now**, **Scope** (in/out), **Approach**, **Risks & mitigations**,
**Definition of done**. Human-readable rationale; committed alongside the ledger.

### Exec-plan ledger (`docs/exec-plans/active/{task-id}.json`)

Top-level: `$schema`, `task_id`, `state` (`draft|active|completed|archived`), `created_at`,
`updated_at`, `evaluator_enabled` (bool, consumed by `#10`), `weights` (per-axis rubric override,
`#12`), `entries[]` (the entry shape above).

### Tech-debt tracker (`docs/exec-plans/tech-debt-tracker.md`)

Single append-only file. Each bullet: `ts` (ISO 8601), `source`
(`verification.failure | refactor-deviation | doc-garden | manual | ...`), `task_id` (if any),
`missing` (one line), `evidence` (pointer). No status field — operator triages by editing.

### Runtime task record (`TaskRecord`, in-memory + `output_file`)

`id`, `type` (`TaskType`), `status` (`TaskStatus`), `description`, `cwd`, `output_file`, `command?`,
`prompt?`, `created_at`, `started_at?`, `ended_at?`, `return_code?`, `metadata`, `env?`, `argv?`.
Listed/filtered via `list_tasks(status=)`; output streamed via `task_output`; terminated via
`stop_task` (→ `killed`).

### Cron manifest (`.harness/cron/{kind}.toml`)

```toml
name = "doc-garden"
enabled = true
schedule = "0 6 * * *"            # croniter-validated cron expression
timezone = "America/New_York"     # optional IANA tz; next_run stored UTC
tier_required = "repo-write"      # sandbox tier (#08)
description = "Find stale docs and open PRs."
entry_prompt = "docs/cron/doc-garden.prompt.md"
max_session_minutes = 30
```

### Cron registry entry (the persisted JSON, `services/cron.py`)

Per job: `name`, `schedule`, `timezone?`, `enabled` (default true), `created_at`, `next_run`
(computed UTC). Stored as a sorted JSON list, written under `exclusive_file_lock`.

### Cron run-record (`docs/cron-runs/{kind}/{YYYY-MM-DD}-{run-id}.json`)

`kind`, `run_id`, `started_at`, `ended_at`, `outcome` (`success | no-op | failed`), `prs_opened[]`
(URLs or local branch refs), `session_jsonl` (pointer).

## Behaviours

### Task lifecycle (durable plan)

1. Planner (`#10`) creates `{task-id}.md` + `.json` in `active/`, state `draft`.
2. The first generator session transitions state to `active`.
3. Generator/evaluator iterate ledger entries (`#10`/`#12`); at most one `in_progress` at a time.
4. When all entries `done` and definition-of-done met, the runner transitions to `completed` and
   moves both files to `completed/` **as part of the session-end commit**.
5. After the retention window, `doc-garden` moves them to `archived/` and records the action.

### Runtime task lifecycle (ephemeral)

1. `create_shell_task`/agent spawn registers a `TaskRecord` (`pending` → `running`), opens its
   `output_file`, and starts a waiter coroutine.
2. Output streams to `output_file`; `task_output` reads incrementally.
3. On exit, the waiter sets `return_code`, transitions to `completed`/`failed`, and calls
   `_notify_completion_listeners`.
4. `stop_task` terminates the process and transitions to `killed`, also notifying listeners.
5. A restarted agent task bumps `_generations`; a restart notice marks the discontinuity in output.

### Completion → durable update (the seam)

1. A registered `CompletionListener` receives the terminal `TaskRecord`.
2. If the task was advancing a ledger entry, the listener updates that entry's `status`/`passes`/
   `notes` and commits the ledger.
3. If the task was a cron session, the listener writes the run-record and fires `cron.run.complete`
   (`#13`).

### Cron registration & scheduling

1. `cron_create`/`upsert_cron_job` validates the `schedule` (croniter) and `timezone` (IANA); invalid
   → rejected.
2. The job is written to the file-locked registry with `enabled=true` and a computed UTC `next_run`.
3. `cron_list`/`cron_toggle`/`cron_delete` manage the registry; toggling `enabled=false` does not
   remove the job.

### Cron kind execution (cron-as-session)

1. The external scheduler fires `harness cron-run {kind}`.
2. Runner loads the manifest; if `enabled=false`, records `cron.skipped` and exits without a worktree.
3. Otherwise it allocates a `task-id`, creates a worktree (`#02`), and spawns a `local_agent`
   `TaskRecord` running a normal session (`#03`) with `entry_prompt` as intent, at the manifest tier.
4. The session runs (validator, orientation, work, sealing); branches/PRs are captured.
5. On completion the listener writes the run-record and fires `cron.run.complete` (`#13`).

### Governance interaction

- A cron job that would modify a governance-tier file opens a `[governance]`-tagged PR instead of
  committing to base; the run-record records the PR.

## Acceptance criteria

### Exec-plan format & FSM (MUST)

1. **MUST** produce both Markdown + JSON when the planner runs.
2. **MUST** validate every ledger against the schema at session start, blocking on schema failure.
3. **MUST** support all four plan states and **move** (never delete) plans `active → completed →
   archived`.
4. **MUST** make the `active → completed` directory move part of the session-end commit.

### Ledger semantics (MUST)

5. **MUST** allow at most one `in_progress` entry per task.
6. **MUST** require `passes = true` for any entry marked `done` by an evaluator-enabled task.
7. **MUST** treat ledger `notes` as append-only.

### Tech-debt tracker (MUST)

8. **MUST** keep `tech-debt-tracker.md` append-only by harness writes.
9. **MUST** include `ts`/`source`/`task_id?`/`missing`/`evidence` in every harness-filed entry.
10. **MUST NOT** act on tech-debt entries autonomously.

### Runtime task model (MUST/SHOULD)

11. **MUST** represent each running task as a `TaskRecord` with a `TaskType`, a `TaskStatus`, and an
    `output_file`.
12. **MUST** transition status through `pending → running → completed|failed|killed` and record a
    `return_code` on natural exit.
13. **MUST** fire registered completion listeners on every terminal transition (including `stop_task`).
14. **SHOULD** support both `command` (shell) and `argv` (shell-bypass) launch forms.
15. **SHOULD** stream task output to `output_file` readable incrementally via `task_output`.

### Completion seam (MUST)

16. **MUST** update (and commit) the durable ledger entry when a task that was advancing it completes.

### Cron registry (MUST)

17. **MUST** validate cron `schedule` (croniter) and `timezone` (IANA) at write time, rejecting
    invalid values.
18. **MUST** compute and store `next_run` in UTC on every upsert.
19. **MUST** write the registry under an exclusive file lock (no torn concurrent writes).

### Default cron kinds & extensibility (MUST/SHOULD)

20. **MUST** ship `doc-garden`, `quality-grade`, `refactor-deviation`, `reference-refresh`, enabled by
    default.
21. **MUST** allow disabling any kind via `enabled=false` (recording `cron.skipped` on next trigger)
    and overriding `schedule`/`tier_required`/`max_session_minutes`.
22. **MUST** discover manifests under `.harness/cron/`; **SHOULD** allow per-repo kinds without code
    changes.

### Cron-as-session (MUST)

23. **MUST** run every cron job as a normal session (same FSM/validator/observability) in its own
    worktree, at the manifest tier.
24. **MUST** record every run in `docs/cron-runs/{kind}/` and fire `cron.run.complete` (`#13`).
25. **MUST** enforce `max_session_minutes` via the turn-timeout machinery, recording
    `outcome: failed (max-session-minutes)` on overrun.

### Governance (MUST)

26. **MUST NOT** auto-merge PRs touching `core-beliefs.md` or `product-specs/`; cron jobs open
    `[governance]`-tagged PRs instead.

## Acceptance scenarios

```gherkin
Scenario: Planner produces both markdown and json
  Given the planner runs for task T1
  When it completes
  Then docs/exec-plans/active/T1.md exists with the required sections
  And docs/exec-plans/active/T1.json validates against the schema.

Scenario: Ledger schema validated at session start
  Given an active plan with a ledger missing the required "entries" field
  When a session starts
  Then the validator emits a blocking finding
  And the session does not start.

Scenario: At most one in_progress entry per task
  Given task T1 has entry A in_progress
  When the generator tries to mark entry B in_progress without finishing A
  Then the runner refuses and records an error event.

Scenario: done requires passes=true under evaluator-enabled tasks
  Given task T1 has evaluator_enabled=true
  When an entry is marked done without a matching pass evaluation
  Then the next session-start validator emits a warning
  And the entry is flagged in the orientation brief.

Scenario: Completed plan moves to completed dir in the seal commit
  Given task T1 reaches definition of done
  When the runner finalises T1
  Then T1.md and T1.json move to docs/exec-plans/completed/
  And the move is part of the session-end commit.

Scenario: Background task completion updates the ledger
  Given a local_agent task was advancing ledger entry A of T1
  When the task exits with return_code 0
  Then its completion listener marks entry A done with passes from the verdict
  And commits the updated ledger.

Scenario: Stopped task notifies listeners
  Given a running task is stopped via stop_task
  Then its status becomes killed
  And completion listeners fire.

Scenario: Invalid cron expression rejected at write
  Given cron_create is called with schedule "not a cron"
  When the registry write is attempted
  Then validate_cron_expression returns false
  And the job is not persisted.

Scenario: next_run computed in UTC honouring timezone
  Given a job scheduled "0 6 * * *" in timezone America/New_York
  When it is upserted
  Then next_run is stored as the corresponding UTC instant.

Scenario: Concurrent cron writes are not torn
  Given two cron_create calls race
  When both write the registry
  Then the exclusive file lock serialises them
  And both jobs are present afterward.

Scenario: Default cron kinds registered
  Given a fresh repo and no overrides
  When the runner lists cron manifests
  Then doc-garden, quality-grade, refactor-deviation, reference-refresh are present and enabled.

Scenario: Disabled cron does not run
  Given quality-grade.toml has enabled=false
  When the scheduler triggers quality-grade
  Then the runner records a cron.skipped event
  And no worktree is created.

Scenario: Cron runs as a normal session in its own worktree
  Given doc-garden triggers
  When the runner starts the cron session
  Then a fresh worktree exists for the run
  And the session follows the standard FSM
  And the session jsonl is sealed at the end.

Scenario: Cron run summary written and hook fired
  Given doc-garden completed and opened 2 PRs
  When the runner records the run
  Then docs/cron-runs/doc-garden/{date}-{run-id}.json lists the 2 PRs and the jsonl pointer
  And cron.run.complete fires on the hook bus.

Scenario: max_session_minutes enforced
  Given doc-garden has max_session_minutes=30
  And its session runs past 30 minutes
  When the cap is reached
  Then the session is aborted via the turn-timeout machinery
  And the run-record outcome is failed with reason max-session-minutes.

Scenario: Operator adds a custom cron kind
  Given .harness/cron/my-thing.toml is added with valid fields
  When the runner discovers manifests
  Then my-thing appears in the registry as enabled
  And triggering it runs a normal session under that manifest's tier.

Scenario: Cron job does not auto-merge governance files
  Given refactor-deviation finds a violation in core-beliefs.md
  When it tries to act
  Then it opens a [governance]-tagged PR instead of committing to base.

Scenario: Tech-debt tracker append-only by harness
  Given an existing tech-debt-tracker.md
  When verification fails twice in one session
  Then two new bullets are appended at the end
  And no existing bullets are modified.
```

## Tests

- `test_plan_markdown_and_json_both_present` — paired files.
- `test_ledger_schema_validated_at_session_start` — schema discipline.
- `test_plan_state_transitions_in_order` — FSM.
- `test_completed_plan_moves_to_completed_dir` — directory move.
- `test_completed_plan_move_part_of_session_commit` — atomic seal.
- `test_archive_after_retention_window` — long-term tidiness.
- `test_at_most_one_in_progress_per_task` — bookkeeping.
- `test_done_without_pass_warns_under_evaluator_enabled` — consistency.
- `test_ledger_notes_append_only` — append discipline.
- `test_task_record_status_transitions` — pending→running→terminal.
- `test_task_return_code_recorded` — exit capture.
- `test_completion_listener_fires_on_natural_exit` — seam.
- `test_completion_listener_fires_on_stop` — killed path.
- `test_task_output_streams_incrementally` — output_file streaming.
- `test_argv_launch_bypasses_shell` — shell-bypass form.
- `test_restart_bumps_generation` — restart distinguishable.
- `test_completion_updates_and_commits_ledger` — durable↔ephemeral seam.
- `test_tech_debt_tracker_append_only_by_harness` — file discipline.
- `test_tech_debt_entry_contains_required_fields` — schema.
- `test_invalid_cron_expression_rejected` — croniter validation.
- `test_invalid_timezone_rejected` — IANA validation.
- `test_next_run_computed_in_utc_with_timezone` — tz correctness.
- `test_cron_registry_write_is_file_locked` — race safety.
- `test_default_cron_kinds_registered` — defaults.
- `test_disabled_cron_records_skipped_event` — disable honoured.
- `test_cron_runs_in_own_worktree` — isolation.
- `test_cron_session_uses_standard_fsm` — same machinery.
- `test_cron_run_summary_written` — observability.
- `test_cron_run_complete_hook_fired` — hook integration (#13).
- `test_custom_cron_kind_discovered` — extensibility.
- `test_custom_cron_kind_runs_under_its_tier` — sandbox honoured.
- `test_max_session_minutes_enforced_by_runner` — cron-level timeout.
- `test_cron_does_not_auto_merge_governance_files` — governance gate.
- `test_cron_pr_for_governance_file_tagged` — auditability.
- `test_overridden_cron_schedule_honoured` — operator control.

## Edge cases

- **A cron session itself triggers a verification failure that files a tech-debt entry.** Allowed;
  the entry is part of the cron session's commit.
- **Two cron jobs fire at the same minute.** Each gets its own worktree (`#02`); they run
  concurrently without blocking each other; the registry write lock serialises only the registry, not
  the runs.
- **A cron job exceeds `max_session_minutes`.** Aborted via the turn-timeout mechanism (`#03`);
  run-record `outcome: failed (max-session-minutes)`.
- **A custom cron manifest declares a nonexistent `tier_required`.** Validator emits a blocking
  finding when the manifest loads; the kind is not scheduled until fixed.
- **Operator manually edits an active ledger to mark something `done`.** Allowed; next session's
  validator flags any `passes` inconsistency as a warning.
- **A `draft` plan never promoted to `active`.** Validator warns after 7 days; no auto-promotion.
- **A background task's process is killed by the OS (OOM) without a clean exit.** The waiter records
  `failed` with the captured `return_code`/signal; the listener still fires so the ledger isn't left
  stuck `in_progress`.
- **A restarted agent task loses interactive context.** A restart notice is written to the
  `output_file` and `_generations` is bumped so the discontinuity is visible.
- **`completed/`/`archived/` accumulate huge file counts.** Filesystem-scale tidiness is out of scope;
  a tarball-archive step is added only if it becomes a problem.

## Open questions

- Whether `quality-grade` writes a single `QUALITY_SCORE.md` or a date-stamped series.
- Whether `reference-refresh` commits to a feature branch (current default) or always opens PRs for
  high-trust references.
- Whether to support "wake on event" triggers (e.g. wake when a PR opens) alongside time-based cron —
  deferred (the `#13` `RemoteTrigger`/`PushNotification` surfaces are the likely home).
- Whether the single-`in_progress` rule is per-task (current) or per-ledger-section.
- Whether completion listeners should be allowed to spawn follow-up tasks (risk: unbounded fan-out;
  current default: listeners update records and fire hooks, they do not spawn).

## Out of scope

- The planner subagent that authors exec-plans (→ `#10`).
- The autopilot pipeline that consumes the ledger as a work queue (→ `#11`).
- The OS scheduler that fires cron triggers (cron/systemd/Task Scheduler) — implementation detail.
- The verification rubric that produces the `passes` verdict (→ `#12`).
- Sandbox tier *definitions* and ramping (→ `#08`); this spec consumes `tier_required`.
- The hook bus `cron.run.complete` rides (→ `#13`).
- The streaming session FSM a cron job runs (→ `#03`).
- The `dream` self-evolution task body (→ `#11`); named here only as a `TaskType`.
- Cross-host cron priority/queuing (single-host in v1, per `#02`).
