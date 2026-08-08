# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Durable session save/resume: `FileSessionStore` under `DreamPaths.sessions_dir`,
  `Session.snapshot` / `restore_from_snapshot`, and `Harness.save_session` /
  `resume_session` so a process restart continues the same transcript (tool-call
  atom intact) with extracted `ToolCallRecord` history.
- Session handle contract for control planes driving the harness across process
  boundaries: `Harness.save_session` returns a `SessionHandle` (session id,
  path, working dir, plus `usage_delta` for the work since the previous save and
  `usage_total`), `start_session(session_id=...)` accepts a caller-minted id so
  a scheduler's task-keyed record and the harness agree without a round-trip,
  and `Harness.reset_session` drops a spent snapshot. The transcript stays in
  dream's own store — a caller persists only the handle.
- `SessionResumeError` with a typed `reason` (`missing` / `corrupt` /
  `schema_mismatch` / `working_dir_mismatch`) and `should_clear_handle`, so a
  failed resume can be recovered (start fresh, clear the handle) without parsing
  messages. `resume_session` refuses a snapshot taken under another working
  directory unless `allow_working_dir_change=True`; snapshots now record
  `working_dir`.
- `SessionHandle`, `SessionSnapshot`, `FileSessionStore`, and
  `SessionResumeError` are public exports.
- `Harness.run_role(session_id=...)` names a role thread so it survives the
  process: the session resumes that snapshot when one is readable and
  `RunRoleResult.session_handle` carries the pointer plus the run's usage delta.
  An unusable snapshot (never written, corrupt, or taken under another working
  directory) starts the thread over under the same name instead of failing the
  run. Omitting `session_id` persists nothing, as before.
- Level-2 ``apply_patch`` (Codex multi-hunk add/update/delete/move) — the sole
  surgical edit tool; former ``edit_file`` removed.
- Spec 05 per-repo tools: discover ``.harness/tools/{name}.toml``, validate
  the strict declaration schema, register as ``ToolSource.PER_REPO`` command
  runners (shadowing a default warns; missing ``risk``/``tier_required`` blocks
  ``build_harness``).

- Powered hooks: `HookSpec.allow_continue`, `HookResult.continue_message` /
  `replacement_result` / `inject_context`, `HookEvent.SUBAGENT_START`.
  `HookExecutor` honors `allow_block` and `allow_continue` (first-wins continue;
  ignored powers emit `hook.blocked.ignored` / `hook.continue.ignored`).
- Session STOP pre-seal continue loop (≤ `max_verify_nudges`, default 3) —
  Hermes-style verify nudge before seal.
- Hermes-style subagent delegate path (`dream.subagents._delegate`): goal+context
  prompt firewall, summary budget + spill; critics route via `spawn_subagent`
  delegate; `test_author` stays inline; `background=true` forced-sync fallback.
- `dream.runner.PlanAdmission` plus the `plan_admission` argument on
  `Harness.run_task` / `dream.runner.run_task`: `RESUME` skips the planner when a
  ledger already exists and continues the sprint loop under the same task id.
- `docs/learnings/` — measured results from running dream against real workloads,
  starting with a 25-task SWE-bench Lite comparison against `opencode` under
  official SWE-bench Docker grading (19/25 vs 21/25).
- `datasets/swe-bench-lite/` — the reproducible benchmark rig behind that write-up:
  runs either harness inside each task's official evaluation container, extracts a
  test-free patch, and grades with `swebench.harness.run_evaluation`.
- CI now also runs on Python 3.14, and the workflow declares least-privilege
  `permissions` plus `concurrency` cancellation.
- Open-source community files: `AGENTS.md`, spec-01 `docs/` tree, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, GitHub issue/PR templates, CI repo-structure check.
- `run_task` failure contract (chorus spec 05 §5): `dream.RunTaskError`
  (carries a typed `phase` of `plan`/`sprint`/`evaluate` plus the original
  `cause`) and `dream.TaskCancelled` (cooperative cancel). A fault inside the
  loop now surfaces as a typed `RunTaskError` naming where it broke;
  `asyncio.CancelledError` and `TaskCancelled` propagate untouched.
- `dream.contracts.__contract_version__` — the cross-repo contract version
  siblings assert against to fail fast on a drifted dream (chorus spec 05 §2).
- Initial repository scaffold: package tree, public API surface,
  cross-repo `dream.contracts` Protocols, test harness pinning the
  exported surface.

### Changed
- Evaluator default tools restore ``query_logs``; ``build_harness`` enables
  the observability pack by default so the name is registered.
- Consumer docs (`HARNESS.md`, `SDK_GUIDE.md`) document the Level-2 default
  surface, opt-in packs, per-repo tools, and that Arceus employee tools stay
  upstream via MCP.
- Web tool descriptions sharpened so ``web_search`` / ``web_fetch`` no longer
  overlap in when-to-use guidance.
- **Breaking:** `default_registry()` is now the Level-2 coding surface only
  (`read_file`, `apply_patch`, `write_file`, `bash`, `git`, `read_offloaded`,
  `glob`, `grep`, `todo_write`, `skill`). Former extras moved to opt-in packs
  via `register_*_tools` / `build_harness` flags (`tasks`, `cron`, `web`,
  `browser`, `worktree`, `code_intel`, `plan`). Pass
  `legacy_surface=True` to restore the previous fat default. Memory and
  observability packs register when `memory=True` / `observability=True`
  (both default on).
- Planner default manifests read via `grep`/`glob` instead of pack-only tools.
- `dream.contracts.__contract_version__` is now `0.6.0` — the hook seam grew
  `HookEvent.SUBAGENT_START`, `HookSpec.allow_continue`, and the new
  `HookResult` reply fields (additive).
- A `needs-changes` verdict no longer escalates the step to `blocked`;
  `NEEDS_CHANGES_LIMIT` now only stops the current `run_task`, and a durable
  block is reserved for `fail`.
- The default `evaluator` role ships with `bash` and `permission_mode="default"`
  so verification runs inside the judging session; the harness-side oracle is
  gone from the evaluator head.

### Removed
- ``edit_file`` / ``FileEditTool`` — use ``apply_patch`` for all surgical edits.
- ``web_extract`` / ``WebExtractTool`` — redundant with ``web_fetch``; use
  ``web_fetch`` for page bodies (and ``browser_run`` when JS is required).

### Fixed
- `test_task_output_streams_incrementally` no longer assumes a subprocess boots
  within a fixed 200 ms window; it polls for the first line instead. The fixed
  sleep made the test flaky on loaded CI runners.
