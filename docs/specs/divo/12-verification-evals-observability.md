# 12 — Verification, Evals & Observability

**One-liner:** Verify like a *user* would — end to end, through real tools — then have a **separate**
critic score it against a declared rubric with explicit weights, and trace every LLM call, tool call,
finding, and verdict in OTel-shaped JSONL. The grader never wrote the code; agent struggle is signal
(a verification failure auto-files a tech-debt entry naming the missing capability); the evaluator is
itself under continuous, operator-approved tuning.

**Sources (source of truth):** `docs/specs/new-specs/07-verification-evals-observability.md` — the
three coupled concerns and their full substance: **verify-like-a-user** (Playwright MCP for any
UI-affecting change; the project's own test runner + smoke scripts for non-UI), the **four-axis scored
rubric** (`design_quality` / `originality` / `craft` / `functionality`, per-task configurable weights,
the **weight-the-model's-worst-axes-higher** bias), **evaluator calibration** (3–5 version-pinned
few-shot examples per session) + the **tuning loop** (operator reviews disagreements → patch the
evaluator prompt → append-only `docs/evals/tuning-log.md`), the **OTel-GenAI-shaped JSONL trace** (one
line per `llm.call` / `tool.call` / `tool.result` / `validator.finding` / `state.transition` /
`evaluation.record`, `span_id`/`parent_span_id` nesting, `gen_ai.*` attributes), the
**agent-queryable** `query_logs` (LogQL-subset) + `query_metrics` (PromQL-subset) tools subject to the
offload contract, **tech-debt auto-filing** on pattern-matched verification failure (conservative
matcher, false-negatives preferred), and the **rolling pass-rate** continuous-monitoring metric — all
carried forward verbatim-in-substance and enriched here, plus the **evaluation-record** /
**rubric-file** / **tuning-log** / **trace-event** / **tech-debt-entry** artefact shapes and the full
acceptance criteria / Gherkin / `test_*` set. That spec's lineage ([ANT-2] verify-then-judge, [OAI]
evaluator-optimiser, [AHE] self-evolution, taxonomy §7/§12) is the conceptual authority. · `#10`
(the **evaluator subagent** that runs this — its own context, read-only toolset = **critic isolation**;
the sprint contract's `verification_steps` this spec defines the meaning of; the `pass | needs-changes
| fail` outcome) · `#03` (the turn loop that *emits* each trace event; the always-on reviewer loop at
session close that runs a lightweight version of this) · `#04` (the offload contract large query
results spill through; compaction never drops a trace) · `#05` (the Playwright MCP + test-runner tools;
the remediation-prompt feedback loop a failing step triggers) · `#07` (the `tech-debt-tracker.md` this
spec appends to; the `prompt-optimize` cron candidate for tuning) · `#11` (the dream phase reads
evaluation records + pass-rate as consolidation signal) · `#01` (trace lines flow into the session
JSONL; the session-end commit carries eval records + tech-debt entries).
**Reference (grounding only, not authority):** [openharness] — `autopilot/types.py`
(`RepoVerificationStep` (`command` / `returncode` / `status` = `success|failed|skipped|error` /
`stdout` / `stderr` — **the concrete shape of one verification step run as a shell command**),
`RepoRunResult` (`verification_steps: list[RepoVerificationStep]`, `verification_report_path`,
`run_report_path`, `assistant_summary` — verification persisted as a report artefact alongside the
run)), the session transcript JSONL + `autopilot/types.py` `RepoJournalEntry`
(`timestamp`/`kind`/`summary`/`task_id`/`metadata`) as the **observability substrate** OpenHarness
actually ships. Used to name the verification-step and verification-report primitives concretely.
**This is the thinnest grounding of any spec, and the divergence is large and deliberate:**
OpenHarness verification is **binary per shell step** (`RepoVerificationStep.status`) feeding the
autopilot FSM's repair loop — it has **no scored multi-axis rubric, no separate calibrated critic, no
OTel-shaped trace schema, and no agent-queryable log/metric tools**. Its "observability" is the raw
session transcript plus the append-only autopilot journal. So new-spec 07 is overwhelmingly the
authority here; OpenHarness grounds only (a) the verification-step-as-shell-command shape and (b) the
verification-report-as-artefact pattern. The scored rubric, critic isolation, calibration/tuning loop,
OTel trace schema, and `query_logs`/`query_metrics` are the conceptual spec's contribution, carried
forward without an implementation precedent to lean on.

---

## Why this matters

Three failures sink a long-running agent's quality, and each maps to one of this spec's concerns:

1. **The author grades itself green.** If the same model context that wrote the code also scores it,
   you get over-confident green ticks — *verification collapse* (`#10`'s second failure mode). → A
   **separate evaluator** (`#10`) with its **own context** and a **read-only toolset** renders the
   verdict. The grader never wrote the code. This is the load-bearing isolation the rubric sits on top
   of.
2. **Unit tests pass, the app is broken.** Verify only through unit tests and you miss the UI bug and
   the integration regression any real user would notice in seconds. → **Verify like a user**: drive
   the *running* app through a browser for UI work, run the project's *actual* test runner + smoke
   scripts for the rest. The surface is the real thing, exercised the real way.
3. **You can't debug, measure, or compare.** Without structured traces you can't debug a failure,
   can't measure whether the agent is improving, and can't tell whether swapping a model changed
   anything. → **Trace everything** in OTel-GenAI-shaped JSONL — one line per call, finding, and
   verdict, with span nesting — so the whole run is inspectable, queryable, and replayable.

The opinionated through-line is **agent struggle is signal.** When verification fails in a way the
runner can pattern-match to a *missing capability* ("no fixture for X", "no skill for Y", "no MCP for
Z"), it auto-files a tech-debt entry (`#07`) naming the gap. Over weeks the harness gets *less* stuck,
because every place it got stuck became a triaged backlog item instead of a silent retry.

OpenHarness proves the *minimum viable* version of this — verification as a list of shell commands,
each pass/fail, feeding a repair loop, with the results written to a report. That is enough to run an
autopilot (`#09`). It is *not* enough to tell good work from barely-passing work, to keep a grader
honest over months, or to answer "why did the agent do that on turn 40." The conceptual spec adds the
scoring, the calibration, and the trace schema that turn pass/fail into a quality system; this spec
carries that, and is honest that there is no shipped precedent for those parts.

## Scope

**In:**
- The verification surface for UI and non-UI work, and how outcomes map onto rubric axes.
- The four-axis rubric: structure, anchors, weights, the worst-axes-higher bias.
- **Critic isolation** as the precondition for trustworthy scoring (own context, read-only tools).
- Evaluator calibration (few-shot, version-pinned) and the operator-approved tuning loop.
- The OTel-GenAI-shaped JSONL trace: event types, span nesting, attribute naming.
- Agent-queryable observability: `query_logs` (LogQL-subset) and `query_metrics` (PromQL-subset),
  offload-aware.
- Tech-debt auto-filing on pattern-matched verification failure (conservative matcher).
- The rolling pass-rate metric (per axis / task / session) surfaced to the dream phase (`#11`).

**Out:**
- The evaluator subagent's *orchestration* — when it spawns, its concurrency, the negotiation
  (→ `#10`).
- The reviewer-loop *mechanics* at session close (→ `#03`; it runs a lightweight version of this).
- Per-project rubric *content/authoring guidelines* (governance; deferred).
- OTel **exporter wiring** to external backends (Honeycomb / Tempo / etc.) — deferred; v1 emits
  OTel-*shaped* JSONL into the session log, it does not ship a collector.
- Dashboard / visualisation surfaces for operators (deferred).
- Multi-tenant evaluator pools (deferred).

## Key decisions (assumed defaults)

1. **UI verification surface = Playwright MCP** (`#05`) driving the running app. The harness MUST
   exercise the user-facing path for any change that could affect UX.
2. **Non-UI verification surface = the project's own test runner** (pytest / jest / cargo-test / …)
   plus any project-declared smoke scripts. Each step is one shell command with a captured
   `returncode` + `stdout`/`stderr` (`RepoVerificationStep`-shaped) and a `status` of
   `success | failed | skipped | error`.
3. **Critic isolation is mandatory.** The evaluator scores in its **own session** (`#10`) with a
   **read-only toolset** (`read_file`, `git`, `bash --read-only`, plus exactly the verification tools
   the contract names). It sees the **diff and the verification artefacts** — the *outcome* — not the
   author's reasoning. The model that wrote the code never scores it in the same context.
4. **Four-axis rubric by default:** `design_quality`, `originality`, `craft`, `functionality`, each
   scored 0–5 against anchored definitions. Weights are per-task configurable via the exec-plan
   ledger.
5. **Weight the model's *worst* axes higher,** not its best. This catches regression where it actually
   matters and resists a model that is fluent-but-wrong.
6. **Evaluator calibration:** every evaluator session loads **3–5 few-shot examples** from
   `docs/evals/calibration/`, each with a scored breakdown it must match in style. Examples are
   **version-pinned to the evaluator prompt**; changing them is itself a tuning event.
7. **Tuning loop is operator-driven and append-only.** The operator reviews evaluation records vs
   their own judgement, files disagreements into `docs/evals/tuning-queue.md`; a `prompt-optimize` job
   (a `#07` cron candidate, operator-triggered in v1) proposes a patch; the operator approves; the
   patch is applied and recorded in `docs/evals/tuning-log.md` with a new `evaluator_version`. **No
   tuning patch is self-applied.**
8. **Trace schema is OTel-GenAI-shaped JSONL.** Exactly one line per `llm.call`, `tool.call`,
   `tool.result` (v1 keeps call/result separate for clarity), `validator.finding`,
   `state.transition`, and `evaluation.record`. Each line carries `ts`, `session_id`, `task_id`,
   `event_type`, `span_id`, `parent_span_id`, and a namespaced `attributes` map using OTel GenAI
   names where they exist (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.prompt_tokens`, …).
   We emit OTel-*shaped* events without requiring a full OTel SDK.
9. **Per-worktree observability stack.** Logs under `.harness/sidecars/{task-id}/logs/`, metrics under
   `.../metrics/`; Playwright artefacts under `.../metrics/playwright/`; verification reports
   alongside (`verification_report_path` / `run_report_path`). All inside the worktree (repo-as-record).
10. **Agent-queryable, offload-aware.** `query_logs(filter, since, until)` exposes a LogQL-subset
    (label matchers + line filter, no joins); `query_metrics(metric, since, until, agg)` exposes a
    PromQL-subset (instant + range, `sum`/`avg`/`max` only). Large results spill through the `#04`
    offload contract.
11. **Tech-debt auto-filing on verification failure.** A *conservative* matcher maps recognised
    failure shapes to a missing-capability bullet appended to `docs/exec-plans/tech-debt-tracker.md`
    (`#07`); when in doubt it files **nothing** and records an info event. The harness never acts on
    tech-debt entries autonomously — the operator triages.
12. **Rolling pass-rate is the continuous-monitoring metric** (per axis / per task / per session),
    surfaced to the operator dashboard (out of scope) and to the dream phase (`#11`) as a quality
    signal.

## Artefact shapes

### Evaluation record (`docs/evals/{task-id}/sprint-{n}.json`)

```
task_id, sprint_number
outcome          : "pass" | "needs-changes" | "fail"
scores           : { axis -> { score: 0-5, weight: float, notes: string } }
weighted_total   : float
rubric_version   : pointer to the rubric file used
evaluator_version: pointer to the evaluator prompt version in the tuning log
verification_runs: [ { tool, command, exit_code, artefact_path } ]   # RepoVerificationStep-derived
created_at
```
Outcome maps onto the ledger per `#10` decision #6 (`pass`→done / `needs-changes`→in_progress /
`fail`→blocked + tech-debt).

### Rubric file (`docs/evals/rubrics/{name}.md`)

Per axis: name + one-paragraph definition · anchor descriptions for scores **0, 2, 5** (endpoints +
midpoint) · default weight.

### Tuning log (`docs/evals/tuning-log.md`) — append-only

Per entry: `ts` · `evaluator_version_before` / `_after` · disagreement summary that triggered the
patch · diff of the prompt change · approving operator.

### Trace event (OTel-shaped JSONL line)

```
ts               : ISO 8601 UTC
session_id, task_id
event_type       : llm.call | tool.call | tool.result | validator.finding
                   | state.transition | evaluation.record
span_id, parent_span_id          # nesting
attributes       : { gen_ai.system, gen_ai.request.model, gen_ai.usage.prompt_tokens, … }
```

### Verification report (`verification_report_path`) — OpenHarness-grounded

The collected `RepoVerificationStep` list for a run: per step `command` / `returncode` / `status` /
truncated `stdout`/`stderr` (full output offloaded per `#04`). Referenced by the evaluation record's
`verification_runs`.

### Tech-debt entry (one bullet in `docs/exec-plans/tech-debt-tracker.md`)

`ts` · `source` = `verification.failure` · `task_id`, `sprint_number` · `missing` (one-line, e.g. "no
fixture for the import wizard") · `evidence` (pointer to the failing run's artefact).

## Behaviours

### UI verification

1. The generator declares a **user path** for the change (one or more URLs/flows).
2. The evaluator session (or the `#03` reviewer at session close) launches Playwright MCP (`#05`).
3. Each user path is exercised; screenshots, console logs, and network traces are written under
   `.harness/sidecars/{task-id}/metrics/playwright/`.
4. Pass/fail is computed against the user-path assertions and feeds the rubric scores.

### Non-UI verification

1. The sprint contract (`#10`) lists `verification_steps` referring to the project's test runner.
2. Each step is invoked from `bash` as a `RepoVerificationStep`; `returncode` + structured output
   (where parseable) collected into the verification report.
3. Failures feed the rubric and emit remediation prompts (`#05`).

### Critic isolation & scoring

- The evaluator runs in its own context with the read-only toolset (decision #3). It receives the diff
  + verification report + the rubric, **not** the generator's working memory or reasoning.
- It scores each axis 0–5 with notes, computes `weighted_total` from the per-task weights, and writes
  exactly one evaluation record. It renders `pass | needs-changes | fail` and **stops** — it does not
  negotiate after the verdict (`#10` criterion #11).

### Evaluator calibration

- Every evaluator session loads 3–5 scored few-shot examples from `docs/evals/calibration/`, pinned to
  the evaluator prompt version. Missing examples → the session refuses to start and records a blocking
  finding (drift mitigation must not silently degrade).

### Tuning loop

1. Operator reviews evaluation records periodically.
2. Disagreements with operator judgement land in `docs/evals/tuning-queue.md`.
3. A `prompt-optimize` job proposes a patch (operator-triggered in v1).
4. Operator approves → patch applied → `tuning-log.md` gains an entry with a new `evaluator_version`.

### Trace emission

- Every LLM call, tool call, tool result, validator finding, state transition, and evaluation record
  produces exactly one JSONL line, flowing into the session JSONL (`#01`).
- `span_id`/`parent_span_id` capture the call hierarchy (an LLM call that triggers a tool call that
  triggers a nested LLM call nests correctly).
- Compaction (`#04`) never drops trace lines — they are durable, not part of the live context window.

### Agent-queryable logs & metrics

- `query_logs(filter, since, until)` → matching events (LogQL-subset). `query_metrics(metric, since,
  until, agg)` → instant/range values (`sum`/`avg`/`max`). Both spill large results through `#04`.

### Tech-debt auto-filing

1. On a verification failure the runner can pattern-match to a missing capability, it appends a
   tech-debt bullet (committed with the session-end commit, `#01`).
2. The matcher is conservative — unrecognised failures file nothing and record an info event.

## Acceptance criteria

### Verification (MUST)

1. **MUST** use a browser-driving tool (Playwright MCP) to verify any UI-affecting change.
2. **MUST** invoke the project's declared test runner for non-UI changes.
3. **MUST** persist verification artefacts (screenshots, logs, traces, reports) under the worktree
   sidecars.
4. **MUST** map verification outcomes onto rubric axes for the evaluator (`#10`).

### Critic isolation (MUST)

5. **MUST** run scoring in a session separate from the one that produced the code, with a read-only
   toolset, seeing the outcome (diff + report) not the author's reasoning.

### Rubric (MUST)

6. **MUST** ship the four default axes.
7. **MUST** allow per-task weight overrides via the exec-plan ledger.
8. **MUST** record `rubric_version` in every evaluation record.

### Calibration & tuning (MUST/SHOULD)

9. **MUST** load 3–5 few-shot examples into every evaluator session.
10. **MUST** version-pin calibration examples to the evaluator prompt; missing examples block the
    session.
11. **MUST** maintain `docs/evals/tuning-log.md` as append-only.
12. **SHOULD** require operator approval to apply a tuning patch; **MUST NOT** self-apply.

### Tracing (MUST)

13. **MUST** emit exactly one JSONL line per LLM call.
14. **MUST** emit exactly one JSONL line per tool call (and one per tool result, in v1).
15. **MUST** emit one JSONL line per validator finding, state transition, and evaluation record.
16. **MUST** align attribute names with OpenTelemetry GenAI conventions where they exist.
17. **MUST** include `span_id` and `parent_span_id` for nestable events.

### Agent-queryable observability (MUST)

18. **MUST** expose `query_logs` and `query_metrics` tools to the agent.
19. **MUST** subject large query results to the `#04` offload contract.

### Tech-debt auto-filing (MUST/SHOULD)

20. **MUST** append a tech-debt entry for every pattern-matched verification failure.
21. **SHOULD** keep the matcher conservative — false negatives preferred to false positives.

### Continuous monitoring (SHOULD)

22. **SHOULD** emit a rolling pass-rate metric (per axis / task / session) consumable by the dream
    phase (`#11`).

## Acceptance scenarios

```gherkin
Scenario: UI change triggers browser verification
  Given a sprint modifies code under src/ui/
  When verification runs
  Then Playwright MCP is launched
  And screenshots are saved under sidecars/{task-id}/metrics/playwright/
  And the evaluation record references at least one playwright artefact.

Scenario: Non-UI change uses the project test runner
  Given a sprint modifies code only under src/lib/
  When verification runs
  Then the project's pytest (or equivalent) is invoked from bash
  And each step is recorded as a verification step with a returncode
  And no Playwright session is started.

Scenario: The grader is a different context than the author
  Given a generator session produced a diff
  When the evaluator scores it
  Then the evaluator runs in its own session with a read-only toolset
  And it receives the diff and verification report, not the generator's working memory.

Scenario: Score uses declared rubric weights
  Given a task overrides weights to {functionality: 3, craft: 2, design_quality: 1, originality: 1}
  When the evaluator scores a sprint
  Then weighted_total uses those weights
  And rubric_version is recorded in the evaluation record.

Scenario: Evaluator session loads calibration examples
  Given docs/evals/calibration/ contains 4 scored examples
  When an evaluator session starts
  Then those 4 examples are present in its first prompt
  And the evaluator version pin matches the calibration version.

Scenario: Missing calibration blocks the evaluator
  Given the calibration directory is empty
  When an evaluator session starts
  Then it refuses to start
  And the runner records a blocking finding.

Scenario: Tuning log appended on patch
  Given operator approves a patch to the evaluator prompt
  When the patch is applied
  Then docs/evals/tuning-log.md gains a new entry
  And the entry records the disagreement summary, diff, and approver
  And no patch was applied without that approval.

Scenario: Each LLM call emits exactly one JSONL event
  Given a turn that makes 3 LLM calls
  When the turn ends
  Then the session jsonl contains exactly 3 events with event_type = llm.call
  And each has gen_ai.* attributes set.

Scenario: Validator finding emitted as trace event
  Given the validator records two warnings at session start
  When the session jsonl is inspected
  Then two events with event_type = validator.finding exist
  And each contains severity, code, and message attributes.

Scenario: Span nesting captures call hierarchy
  Given an LLM call A makes a tool call B which makes another LLM call C
  When the events are inspected
  Then C's parent_span_id equals B's span_id
  And B's parent_span_id equals A's span_id.

Scenario: Agent queries its own logs
  Given the agent calls query_logs(filter={"event_type": "tool.call"}, since=-1h)
  When the tool returns
  Then the result is a list of matching events
  And if larger than the offload threshold it is offloaded per #04.

Scenario: Verification failure files tech-debt entry
  Given verification fails because "no fixture for the import wizard"
  When the runner pattern-matches the failure
  Then a new bullet is appended to docs/exec-plans/tech-debt-tracker.md
  And the bullet references the failing run's artefact path.

Scenario: Conservative matcher leaves unrecognised failures alone
  Given verification fails with an opaque stack trace the matcher does not recognise
  When the runner inspects the failure
  Then no tech-debt entry is filed
  And an info event records that no pattern matched.
```

## Tests

- `test_ui_change_triggers_playwright`
- `test_non_ui_change_uses_project_test_runner`
- `test_verification_step_captures_returncode_and_status` — `RepoVerificationStep`-shaped.
- `test_playwright_artefacts_written_to_sidecars`
- `test_verification_outcomes_mapped_to_rubric_axes`
- `test_evaluator_runs_in_separate_context_from_author` — critic isolation.
- `test_evaluator_toolset_is_read_only`
- `test_evaluator_sees_outcome_not_author_reasoning`
- `test_default_rubric_has_four_axes`
- `test_per_task_weight_override_honoured`
- `test_evaluation_record_includes_rubric_version`
- `test_evaluation_record_includes_evaluator_version`
- `test_evaluator_session_loads_calibration_examples`
- `test_calibration_examples_version_pinned`
- `test_missing_calibration_blocks_evaluator`
- `test_tuning_log_append_only`
- `test_tuning_patch_requires_operator_approval`
- `test_tuning_patch_never_self_applied`
- `test_one_event_per_llm_call`
- `test_one_event_per_tool_call`
- `test_one_event_per_tool_result`
- `test_validator_finding_emitted_as_trace_event`
- `test_state_transition_emitted_as_trace_event`
- `test_evaluation_record_emitted_as_trace_event`
- `test_trace_attributes_use_otel_genai_names`
- `test_span_nesting_correct`
- `test_trace_lines_survive_compaction` — `#04` interaction.
- `test_query_logs_returns_matching_events`
- `test_query_metrics_supports_sum_avg_max`
- `test_large_query_result_offloaded`
- `test_verification_failure_files_tech_debt_entry`
- `test_unmatched_verification_failure_files_nothing`
- `test_tech_debt_entry_references_run_artefact`
- `test_evaluation_record_persisted_under_docs_evals`
- `test_rolling_pass_rate_metric_emitted`

## Edge cases

- **Playwright unavailable** (MCP not on the allowlist or crashes). Verification falls back to non-UI
  tests; the runner emits a warning event; the operator is alerted via the session summary. UI
  assertions are recorded as `skipped`, not silently passed.
- **Test runner emits no structured output.** The verification step records `returncode` only;
  remediation prompts (`#05`) can't include pointers; the agent inspects offloaded `stdout`/`stderr`
  manually.
- **Evaluator few-shot examples missing.** Session refuses to start; blocking finding recorded
  (decision #6 / criterion #10) — drift mitigation never silently degrades.
- **Two evaluators disagree** (reviewer + sprint evaluator on the same change). Both records persist;
  the disagreement surfaces on the operator dashboard. No automatic tie-break here (`#10` open
  question).
- **OTel attribute name added upstream after we shipped.** Added on next release; existing events are
  not retroactively rewritten.
- **`query_logs` returns 0 events** (filter too narrow). Returns empty list, no offload; the agent
  decides next.
- **Verification "passes" but the model wrote tests that assert nothing.** Out of this spec's reach in
  v1 — caught (if at all) by the `craft` axis and the reviewer; flagged as an open question for a
  mutation-testing surface.
- **A failing step's output is enormous.** Truncated in the report; full output offloaded per `#04`;
  the trace event carries the offload pointer, not the bytes.

## Open questions

- Whether to ship a default rubric specialised for code-review-style work (current: four general
  axes).
- Whether the tuning loop should auto-*suggest* patches or remain fully operator-driven (current:
  operator-driven; self-apply forbidden).
- Whether to dedupe `tool.call` + `tool.result` into a single trace event in v1 (current: separate for
  clarity).
- Whether to support exemplars (failing test + expected fix) in the calibration set.
- Whether a mutation-testing or assertion-coverage surface is needed to catch vacuous tests.

## Out of scope

- The evaluator subagent's orchestration / concurrency / negotiation (→ `#10`).
- The reviewer loop's lightweight verification at session close (→ `#03`).
- Per-project rubric authoring guidelines (governance; deferred).
- OTel exporter wiring to external backends (Honeycomb / Tempo) — v1 emits OTel-shaped JSONL only.
- Dashboard / visualisation surfaces for operators (deferred).
- Multi-tenant evaluator pools (deferred).
