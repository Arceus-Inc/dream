# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- OpenTelemetry is **default-on**: core deps ship the OTLP SDK; sessions fan
  JSONL traces to OTel (`CompositeTracer`). Endpoint defaults to
  `http://localhost:4318`; override with `OTEL_EXPORTER_OTLP_ENDPOINT`. Opt out
  with `OTEL_SDK_DISABLED=true` (JSONL only). Process shutdown waits at most 5s
  when no collector is listening; JSONL is unaffected. See
  `docs/specs/divo/otel-architecture-gap.md` and `evals/otel/`.
- `RunTrace.read()` aggregates a session's existing JSONL `TraceEvent` stream
  into an immutable typed value. Nested JSON on snapshots and traces is captured
  as public `FrozenJsonObject` / `FrozenJsonArray` values via `capture` /
  `freeze_json_value`; `thaw` / `thaw_json_value` restore plain lists and dicts
  for live `SessionOptions` and the JSON codec.
- `SprintRunResult.evaluation` exposes the typed `EvaluationRecord | None`, and
  `USER_PROMPT_SUBMIT` hook payloads include the configured role when present.
- Durable session save/resume: `FileSessionStore` under `DreamPaths.sessions_dir`,
  `Session.snapshot` / `restore_from_snapshot`, and `Harness.save_session` /
  `resume_session` so a process restart continues the same transcript (tool-call
  atom intact) with extracted `ToolCallRecord` history.
- Session handle contract for control planes driving the harness across process
  boundaries: `Harness.save_session` returns a `SessionHandle` (session id,
  path, working dir, plus `usage_delta` for the work since the previous save and
  `usage_total`),   `start_session(session_id=...)` accepts a caller-minted id so
  a scheduler's task-keyed record and the harness agree without a round-trip,
  and `Harness.reset_session` drops a spent snapshot. An id that already names
  a saved snapshot is refused, so two callers landing on the same key get an
  error rather than saving over each other; `resume_session` continues it and
  `reset_session` discards it. The transcript stays in dream's own store — a
  caller persists only the handle.
- `SessionResumeError` with a typed `reason` (`missing` / `corrupt` /
  `schema_mismatch` / `working_dir_mismatch`) and `should_clear_handle`, so a
  failed resume can be recovered (start fresh, clear the handle) without parsing
  messages. `resume_session` refuses a snapshot taken under another working
  directory unless `allow_working_dir_change=True`; snapshots record
  `working_dir` at schema version 2, and a version-1 file — written before the
  field existed, so with no directory to check — is refused as a
  `schema_mismatch` rather than resumed anywhere.
- `SessionHandle`, `SessionSnapshot`, `FileSessionStore`, and
  `SessionResumeError` are public exports.
- `Harness.run_role(session_id=...)` names a role thread so it survives the
  process: the session resumes that snapshot when one is readable and
  `RunRoleResult.session_handle` carries the pointer plus the run's usage delta.
  A spent snapshot (never written, or corrupt) starts the thread over under the
  same name instead of failing the run. A snapshot taken under another working
  directory is left alone — the run gets a fresh unnamed session and no handle,
  so the transcript stays resumable from the workspace that wrote it. Omitting
  `session_id` persists nothing, as before.
- `Harness.run_task(session_scope=...)` makes a whole task resumable off one
  key: each autowired head runs in its own thread under that scope
  (`{scope}-planner`, `{scope}-generator`, `{scope}-evaluator`), so a later
  call with the same scope continues those conversations instead of restarting
  them. Explicitly supplied heads are untouched.
- `LedgerStep.acceptance_criteria`: the planner names what "done" means for each
  step alongside the step itself, and the planner response schema now requires
  at least one criterion per step.
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
- **Breaking:** `SessionSnapshot.messages`, message `content`, and `tool_calls`
  are tuples. `SessionSnapshot.metadata`, tool-use / tool-call `input`, and
  `TraceEvent.attributes` are `FrozenJsonObject` (nested arrays are
  `FrozenJsonArray`) rather than mutable dicts and lists. Direct FrozenJson
  constructors recursively seal nested values; object equality ignores key
  order. Resume still thaws snapshot metadata into live `SessionOptions`.
  Malformed non-mapping trace `attributes` decode as an empty frozen object.
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
- **Breaking:** the sprint contract is built from the plan instead of being
  negotiated. `run_task` takes three heads (`planner`, `generator_execute`,
  `evaluator_run`) rather than five; the contract for a sprint is assembled from
  the ledger step's `acceptance_criteria` plus any items a prior
  `needs-changes` verdict left unresolved. This removes two to six LLM role
  sessions per sprint that ran before any code was written — a typical sprint
  costs two sessions instead of four. `SprintContract` drops `imposed` and
  `negotiation_log`; older contract files still load, those fields are ignored.

### Removed
- `dream.sprint.negotiate_contract` / `negotiate_contract_async` /
  `build_contract_from_negotiation` / `NegotiationResult` / `NegotiationEntry` /
  `EvaluatorPropose` / `GeneratorRespond`, and the
  `make_evaluator_propose_head` / `make_generator_respond_head` factories with
  their parse errors. Use `dream.sprint.build_contract_from_step`.
- ``edit_file`` / ``FileEditTool`` — use ``apply_patch`` for all surgical edits.
- ``web_extract`` / ``WebExtractTool`` — redundant with ``web_fetch``; use
  ``web_fetch`` for page bodies (and ``browser_run`` when JS is required).

### Fixed
- `test_task_output_streams_incrementally` no longer assumes a subprocess boots
  within a fixed 200 ms window; it polls for the first line instead. The fixed
  sleep made the test flaky on loaded CI runners.
