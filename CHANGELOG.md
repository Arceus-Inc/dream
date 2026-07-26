# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Fixed
- `test_task_output_streams_incrementally` no longer assumes a subprocess boots
  within a fixed 200 ms window; it polls for the first line instead. The fixed
  sleep made the test flaky on loaded CI runners.
