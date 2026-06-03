# 09 — Task Engine & Cron

**One-liner:** Tasks are exec-plans (versioned Markdown + ledger). Tech-debt is a single rolling file. Cron jobs do the recurring housekeeping, each as a normal harness session.

**Sources:** [OAI], [ANT-1] · taxonomy §14, §2

---

## Why this matters

A harness that only runs when an operator types at a terminal is barely a system; it's an assistant. The capability gap between "tool you invoke" and "system that maintains itself" closes when you add two things:

1. **A persistent task representation** that survives across sessions, can be paused and resumed, and tracks progress at a granularity finer than "session done."
2. **A scheduler** that wakes the harness on its own to do the recurring work nobody enjoys doing manually — refresh stale docs, regrade quality, scan for drift from core beliefs, re-fetch vendored references.

This spec defines both. Tasks are deliberately filesystem-shaped (Markdown + JSON in `docs/exec-plans/`) so they're inspectable in any text editor and live diff-able in code review. Cron jobs are deliberately just-another-session so the same FSM, validator, and observability apply.

## Scope

**In:** exec-plan format (Markdown narrative + JSON ledger), plan lifecycle, tech-debt tracker, default cron job kinds and their cadence, cron-as-session execution model, operator extensibility for new cron kinds.

**Out:** the planner subagent that creates plans (→ #06); the underlying scheduler infrastructure (OS cron / Windows Task Scheduler — wiring is implementation detail); merge/PR-promotion strategy for completed tasks (deferred).

## Key decisions (assumed defaults)

1. **One Markdown + one JSON per active task.** Narrative goes in `docs/exec-plans/active/{task-id}.md`. Machine-readable state goes in `docs/exec-plans/active/{task-id}.json` (the ledger).
2. **Ledger schema declared in `docs/_schemas/exec-plan-ledger.schema.json`.** Validated by the session-start validator (#01).
3. **Ledger entry shape:**
   - `id` — stable within the plan.
   - `description` — one sentence.
   - `steps` — ordered list of sub-step strings.
   - `status` — `pending | in_progress | done | blocked`.
   - `passes` — boolean; whether last evaluation passed (#07).
   - `notes` — append-only free text.
4. **Plan states:** `draft → active → completed → archived`. Completed plans move to `docs/exec-plans/completed/`; archived (older than 90 days) move to `docs/exec-plans/archived/`.
5. **Tech-debt tracker is a single rolling file:** `docs/exec-plans/tech-debt-tracker.md`. Append-only. Operator triages; the harness does not act on entries autonomously.
6. **Four default cron kinds shipped:**
   - `doc-garden` — daily; scans for stale docs (older than 30 days untouched + referenced from `AGENTS.md`); opens PRs proposing fixes.
   - `quality-grade` — weekly; runs the quality rubric (#07) on recent commits; writes results to `docs/QUALITY_SCORE.md`.
   - `refactor-deviation` — daily; scans for code patterns that violate `core-beliefs.md` (#08); opens small PRs.
   - `reference-refresh` — weekly; re-fetches vendored docs in `docs/references/`; commits diffs.
7. **Cron jobs run as normal harness sessions** (#03) in their own worktrees (#02). Same FSM, same validator, same observability, same sandbox tier as a generator task.
8. **Cron kind defined by a manifest** under `.harness/cron/{kind}.toml`. Operators can add new kinds without forking.
9. **Operator can disable any cron kind** via a single flag (`enabled = false`) without removing the manifest.
10. **Cron jobs never auto-merge** governance-tier files (#08, #10). They open PRs; humans merge.

## Artefact shapes

### Exec-plan Markdown (`docs/exec-plans/active/{task-id}.md`)

Sections:
- **Goal** — one paragraph.
- **Why now** — one paragraph.
- **Scope** — bullet list (in/out).
- **Approach** — short prose describing the strategy.
- **Risks & mitigations** — bullets.
- **Definition of done** — bullets.

The Markdown is the human-readable rationale; the ledger is the machine-readable state. They are committed together.

### Exec-plan ledger (`docs/exec-plans/active/{task-id}.json`)

Top-level fields:
- `$schema`
- `task_id`
- `state` — `draft | active | completed | archived`
- `created_at`, `updated_at`
- `evaluator_enabled` — boolean (consumed by #06)
- `weights` — per-axis rubric weights override (consumed by #07)
- `entries` — list of ledger entries (shape above)

### Tech-debt tracker (`docs/exec-plans/tech-debt-tracker.md`)

Single file, append-only. Each entry is a bullet:
- `ts` — ISO 8601.
- `source` — `verification.failure | refactor-deviation | doc-garden | manual | ...`
- `task_id` (if applicable).
- `missing` — one-line description.
- `evidence` — pointer to a file or run artefact.

No status field — the operator triages by editing the file directly (delete, move to a backlog, file an exec-plan).

### Cron manifest (`.harness/cron/{kind}.toml`)

```toml
name = "doc-garden"
enabled = true
schedule = "0 6 * * *"          # cron expression
tier_required = "repo-write"     # sandbox tier (#08)
description = "Find stale docs and open PRs."
entry_prompt = "docs/cron/doc-garden.prompt.md"
max_session_minutes = 30
```

### Cron run record

Each cron run produces a normal session jsonl + a small summary file at `docs/cron-runs/{kind}/{YYYY-MM-DD}-{run-id}.json`:
- `kind`, `run_id`, `started_at`, `ended_at`
- `outcome` — `success | no-op | failed`
- `prs_opened` — list of PR URLs (or local branch refs)
- `session_jsonl` — pointer

## Behaviours

### Task lifecycle

1. Planner (#06) creates `{task-id}.md` + `{task-id}.json` in `active/`, with state `draft`.
2. On first generator session, state transitions to `active`.
3. Generator + evaluator iterate through ledger entries (#06).
4. When all entries `done` and definition-of-done satisfied, runner transitions state to `completed` and moves both files to `completed/`.
5. After 90 days in `completed/`, the `archive-old-plans` step (part of `doc-garden`) moves them to `archived/`.

### Plan validation at session start

- Validator (#01) checks every ledger file against the schema.
- Validator checks every `active/` plan was touched in the last 7 days (warning, not blocking, per #01).
- Validator checks that every `done` step has `passes = true` from at least one evaluation record (#07); inconsistency emits a warning.

### Tech-debt entry creation

- Verification failures auto-file via the pattern matcher (#07).
- Cron jobs may file entries directly when they detect issues (e.g. `refactor-deviation` files entries for patterns it can't safely fix automatically).
- Manual entries are allowed (operator edits the file).

### Cron kind execution

1. External scheduler triggers `harness cron-run {kind}`.
2. Runner loads the manifest, allocates a `task-id`, creates a worktree (#02), starts a session (#03) with the manifest's `entry_prompt` as the initial intent.
3. Sandbox tier is set per the manifest.
4. Session runs normally (validator, orientation, work turns, sealing).
5. Any branches/PRs the session created are recorded in the run summary.
6. Runner writes the run summary, fires `cron.run.complete` hook (#12).

### Disabling a cron kind

- Operator sets `enabled = false` in the manifest.
- Next scheduled trigger: runner detects the flag, records a `cron.skipped` event, exits cleanly.

### Custom cron kind

- Operator drops a new manifest under `.harness/cron/`.
- Operator schedules the trigger externally (cron entry, Task Scheduler entry, etc.).
- Custom kinds use the same execution model as defaults.

## Acceptance criteria

### Exec-plan format (MUST)

1. **MUST** produce both Markdown + JSON when planner runs (per #06).
2. **MUST** validate every ledger against the schema at session start (#01).
3. **MUST** support all four plan states.
4. **MUST** move plans from `active/` to `completed/` on completion, not delete.
5. **MUST** move plans from `completed/` to `archived/` after the configured retention (default 90 days).

### Ledger semantics (MUST)

6. **MUST** allow exactly one ledger entry to be `in_progress` at a time within a single task.
7. **MUST** require `passes = true` for any entry marked `done` by an evaluator-enabled task.
8. **MUST** treat ledger `notes` as append-only.

### Tech-debt tracker (MUST)

9. **MUST** keep `tech-debt-tracker.md` append-only by harness writes.
10. **MUST** include ts, source, task_id (if any), missing, and evidence in every harness-filed entry.
11. **MUST NOT** act on tech-debt entries autonomously.

### Default cron kinds (MUST)

12. **MUST** ship the four default kinds: `doc-garden`, `quality-grade`, `refactor-deviation`, `reference-refresh`.
13. **MUST** allow operators to disable any default kind via the `enabled` flag.
14. **MUST** allow operators to override `schedule`, `tier_required`, and `max_session_minutes` per kind.

### Cron-as-session (MUST)

15. **MUST** run every cron job as a normal harness session (same FSM, same validator, same observability).
16. **MUST** isolate every cron job in its own worktree.
17. **MUST** record every cron run in `docs/cron-runs/{kind}/`.
18. **MUST** fire `cron.run.complete` on the hook bus (#12).

### Cron extensibility (MUST/SHOULD)

19. **MUST** discover cron manifests under `.harness/cron/`.
20. **SHOULD** allow per-repo cron kinds without harness code changes.

### Governance interactions (MUST)

21. **MUST NOT** auto-merge PRs touching `core-beliefs.md` or `product-specs/` (delegated to #10 enforcement, but cron jobs must respect it).

## Acceptance scenarios

```gherkin
Scenario: Planner produces both markdown and json
  Given the planner runs for task T1
  When it completes
  Then docs/exec-plans/active/T1.md exists with the required sections
  And docs/exec-plans/active/T1.json exists and validates against the schema.

Scenario: Ledger schema validated at session start
  Given an active plan with a ledger missing the required "entries" field
  When a session starts
  Then the validator emits a blocking finding
  And the session does not start.

Scenario: At most one in_progress entry per task
  Given task T1 has entry A in_progress
  When the generator tries to mark entry B in_progress without finishing A
  Then the runner refuses
  And an error event is recorded.

Scenario: done requires passes=true under evaluator-enabled tasks
  Given task T1 has evaluator_enabled = true
  When the generator marks an entry done without a matching pass evaluation
  Then the validator (at next session start) emits a warning
  And the entry's status is flagged in the orientation brief.

Scenario: Completed plan moves to completed dir
  Given task T1 reaches definition of done
  When the runner finalises T1
  Then T1.md and T1.json move to docs/exec-plans/completed/
  And the move is part of the session-end commit.

Scenario: Plans older than retention move to archived
  Given T1 has been in completed/ for 91 days
  When the doc-garden cron runs
  Then T1.md and T1.json move to docs/exec-plans/archived/
  And the run summary records the archive action.

Scenario: Default cron kinds registered
  Given a fresh repo and no operator overrides
  When the runner lists cron manifests
  Then doc-garden, quality-grade, refactor-deviation, reference-refresh are all present and enabled.

Scenario: Disabled cron does not run
  Given quality-grade.toml has enabled = false
  When the external scheduler triggers quality-grade
  Then the runner records a cron.skipped event
  And no worktree is created.

Scenario: Cron runs in its own worktree with normal session
  Given doc-garden triggers
  When the runner starts the cron session
  Then a fresh worktree exists for the run
  And the session follows the standard FSM (#03)
  And the session jsonl is sealed at the end.

Scenario: Cron run summary written
  Given doc-garden completed and opened 2 PRs
  When the runner records the run
  Then docs/cron-runs/doc-garden/{date}-{run-id}.json exists
  And it lists the 2 PRs and the session jsonl pointer.

Scenario: Operator adds a custom cron kind
  Given .harness/cron/my-thing.toml is added with valid fields
  When the runner discovers manifests
  Then my-thing appears in the registry as enabled
  And triggering it runs a normal session under that manifest's tier.

Scenario: Cron job does not auto-merge governance files
  Given refactor-deviation finds a violation in core-beliefs.md
  When it tries to act
  Then it opens a PR instead of committing to the base branch
  And the PR is flagged with [governance] in its title.

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
- `test_completed_plan_move_part_of_session_commit` — atomic.
- `test_archive_after_retention_window` — long-term tidiness.
- `test_at_most_one_in_progress_per_task` — bookkeeping.
- `test_done_without_pass_warns_under_evaluator_enabled` — consistency.
- `test_ledger_notes_append_only` — append discipline.
- `test_tech_debt_tracker_append_only_by_harness` — file discipline.
- `test_tech_debt_entry_contains_required_fields` — schema.
- `test_default_cron_kinds_registered` — defaults.
- `test_disabled_cron_records_skipped_event` — disable honoured.
- `test_cron_runs_in_own_worktree` — isolation.
- `test_cron_session_uses_standard_fsm` — same machinery.
- `test_cron_run_summary_written` — observability.
- `test_cron_run_complete_hook_fired` — hook integration (#12).
- `test_custom_cron_kind_discovered` — extensibility.
- `test_custom_cron_kind_runs_under_its_tier` — sandbox honoured.
- `test_cron_does_not_auto_merge_governance_files` — governance gate.
- `test_cron_pr_for_governance_file_tagged` — auditability.
- `test_overridden_cron_schedule_honoured` — operator control.
- `test_max_session_minutes_enforced_by_runner` — cron-level timeout.
- `test_validator_warns_on_stale_active_plan` — soft signal.
- `test_validator_warns_on_done_without_pass_record` — soft signal.

## Edge cases

- **A cron job's session itself triggers a verification failure that files a tech-debt entry.** Allowed; the entry is part of the cron session's commit.
- **Two cron jobs scheduled to start at the same minute.** Each gets its own worktree (#02); they run concurrently, never block each other.
- **A cron job exceeds `max_session_minutes`.** Runner aborts the session via the turn-timeout mechanism (#03); the run summary records `outcome: failed` with reason `max-session-minutes`.
- **A custom cron manifest declares a nonexistent `tier_required`.** Validator emits a blocking finding when the manifest is loaded; the cron is not scheduled until fixed.
- **Operator manually edits an active ledger to mark something done.** Allowed; next session's validator notices if `passes` is inconsistent and emits a warning.
- **A `draft` plan is never promoted to `active`.** Validator emits a warning after 7 days; no auto-promotion.
- **`completed/` and `archived/` accumulate huge numbers of files.** Filesystem-level scaling is out of scope; if it becomes a problem, we add a tarball-archive step.

## Open questions

- Whether `quality-grade` cron should write to a single `QUALITY_SCORE.md` or a date-stamped series.
- Whether `reference-refresh` should commit changes directly to a feature branch (current default) or open PRs for human review (likely required for high-trust references).
- Whether to support a "wake on event" trigger (e.g. wake when a PR is opened) in addition to time-based cron — deferred.
- Whether the in_progress-uniqueness rule should be per-task or per-ledger-section (current: per-task).

## Out of scope

- The scheduler infrastructure itself (OS cron, systemd timers, Task Scheduler) — implementation detail.
- Merge / PR-promotion policy for completed task work (deferred; for now, runner opens a PR and humans merge).
- Cron job priority / queuing across hosts (deferred — single-host in v1, per #02).
- Dashboard for plan/cron observability (deferred; operators read the files).
- AI-driven re-planning mid-task (out of scope; see #06's "operator opens a new task" rule).
