# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `resume_messages` on `Harness.start_session`, `Harness.run_role`, and `Harness.run_task` (autowired generator) so callers can seed typed transcript from a durable store (chorus ledger / FileSessionStore) instead of intent-string injection.
- Public `dream.messages` types: `ConversationMessage`, `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ImageBlock`.
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
- `dream.contracts.__contract_version__` is now `0.6.0` — the hook seam grew
  `HookEvent.SUBAGENT_START`, `HookSpec.allow_continue`, and the new
  `HookResult` reply fields (additive).
- A `needs-changes` verdict no longer escalates the step to `blocked`;
  `NEEDS_CHANGES_LIMIT` now only stops the current `run_task`, and a durable
  block is reserved for `fail`.
- The default `evaluator` role ships with `bash` and `permission_mode="default"`
  so verification runs inside the judging session; the harness-side oracle is
  gone from the evaluator head.
- `edit_file` refuses an ambiguous (multi-match) edit instead of replacing the
  first occurrence — pass `replace_all=true` to apply to every match.

### Fixed
- `test_task_output_streams_incrementally` no longer assumes a subprocess boots
  within a fixed 200 ms window; it polls for the first line instead. The fixed
  sleep made the test flaky on loaded CI runners.
