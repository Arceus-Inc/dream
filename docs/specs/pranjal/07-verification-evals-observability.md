# 07 — Verification, Evals & Observability

**One-liner:** Verify like a user would — end to end, through real tools. Score against a declared rubric with explicit weights. Trace everything in OTel-shaped JSONL. Tune the evaluator continuously.

**Sources:** [ANT-2], [OAI], [AHE] · taxonomy §7, §12

---

## Why this matters

If an agent grades its own output with the same model that produced it, you get over-confident green ticks. If you verify only through unit tests, you miss UI bugs and integration regressions that any real user would notice in seconds. If you don't trace the agent's decisions, you can't debug failures, can't measure improvement, and can't tell whether a model swap actually changed anything.

This spec pins down three coupled concerns:

1. **Verification surface** — run the actual thing the way an actual user would.
2. **Evaluation** — score that verification against a declared rubric, with the evaluator itself under continuous tuning.
3. **Observability** — emit structured traces so all of the above is inspectable, queryable, and replayable.

The opinionated default is that **agent struggle is signal** — when the agent fails to verify, that failure should auto-file a tech-debt entry naming the missing capability (#09), so the harness gets less stuck over time.

## Scope

**In:** verification surface for UI and non-UI work, rubric structure and weights, evaluator calibration and tuning loop, OTel-shaped event schema, agent-queryable logs/metrics, tech-debt auto-filing on verification failure.

**Out:** the evaluator subagent's orchestration (→ #06); reviewer-loop mechanics at session close (→ #03); rubric *content* per project (governance); OTel exporter wiring to external systems (→ #11).

## Key decisions (assumed defaults)

1. **Verification surface for UI work:** Playwright MCP drives the running app. The harness MUST exercise the user-facing path for any change that could affect UX.
2. **Verification surface for non-UI work:** the project's own test runner (pytest/jest/cargo-test/etc.) plus any project-declared smoke scripts.
3. **Rubric structure:** four axes by default — `design_quality`, `originality`, `craft`, `functionality`. Weights configurable per task.
4. **Weight default bias:** weight axes the model is *worst* at higher, not the ones it's best at. This catches regression where it matters.
5. **Evaluator calibration:** seed every evaluator session with 3–5 few-shot examples paired with detailed score breakdowns. Drift mitigation.
6. **Evaluator tuning loop:** operator reviews evaluator output vs operator's own judgement; diffs patch the evaluator's prompt; patches recorded in `docs/evals/tuning-log.md`.
7. **Trace schema:** one JSONL event per LLM call, tool call, validator finding, state transition, evaluation record. Schema aligned with OpenTelemetry GenAI semantic conventions (attributes only — we don't need a full OTel SDK to emit OTel-shaped events).
8. **Per-worktree observability stack:** logs under `.harness/sidecars/{task-id}/logs/`, metrics under `.../metrics/`. Agent can query both via tools that present LogQL/PromQL-shaped APIs (small, opinionated subsets).
9. **Tech-debt auto-filing:** every verification failure files an entry in `docs/exec-plans/tech-debt-tracker.md` naming the missing tool, skill, doc, or fixture. Operator triages; the harness doesn't act on tech-debt entries autonomously.
10. **Continuous monitoring metric:** rolling pass-rate per axis, per task, per session. Surfaced on the operator's dashboard (out of scope here) and to the dream phase (#10).

## Artefact shapes

### Evaluation record (`docs/evals/{task-id}/sprint-{n}.json`)

Fields:
- `task_id`, `sprint_number`
- `outcome` — `pass | fail | needs-changes`
- `scores` — map of axis → `{score: 0-5, weight: float, notes: string}`
- `weighted_total` — float
- `rubric_version` — pointer to the rubric file used
- `evaluator_version` — pointer to the evaluator prompt version in the tuning log
- `verification_runs` — list of `{tool, command, exit_code, artefact_path}`
- `created_at`

### Rubric file (`docs/evals/rubrics/{name}.md`)

Sections per axis:
- Axis name and one-paragraph definition.
- Anchor descriptions for scores 0, 2, 5 (clear endpoints + midpoint).
- Default weight.

### Tuning log (`docs/evals/tuning-log.md`)

Append-only entries:
- Timestamp, evaluator-version-before, evaluator-version-after.
- Disagreement summary that triggered the patch.
- Diff of the prompt change.
- Operator who approved.

### Trace event (OTel-shaped jsonl)

Required top-level fields:
- `ts` — ISO 8601 UTC.
- `session_id`, `task_id`.
- `event_type` — `llm.call | tool.call | tool.result | validator.finding | state.transition | evaluation.record`.
- `span_id`, `parent_span_id` — for nesting.
- `attributes` — namespaced map following OTel GenAI conventions where applicable (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.prompt_tokens`, etc.).

### Tech-debt entry

A single bullet appended to `docs/exec-plans/tech-debt-tracker.md`:
- `ts` — when filed.
- `source` — `verification.failure` (this spec) or other.
- `task_id`, `sprint_number`.
- `missing` — one-line description (e.g. "no fixture for the import wizard").
- `evidence` — pointer to the failing run.

## Behaviours

### UI verification

1. Generator declares a "user path" for the change (one or more URLs/flows).
2. Evaluator session (or reviewer at session close) launches Playwright MCP (#05).
3. Each user path is exercised; screenshots, console logs, and network traces are written under `.harness/sidecars/{task-id}/metrics/playwright/`.
4. Pass/fail is computed against the user-path assertions; results feed the scores.

### Non-UI verification

1. The sprint contract (#06) lists `verification_steps` referring to the project's test runner.
2. Each step is invoked from `bash`; exit code + structured output (where parseable) collected.
3. Failures feed the rubric and emit remediation prompts (#05).

### Evaluator calibration

- Every evaluator session loads its 3–5 few-shot examples from `docs/evals/calibration/`.
- The examples include a *scored* breakdown the evaluator must match in style.
- Calibration examples are version-pinned by the evaluator prompt; updating examples is itself a tuning event.

### Tuning loop

1. Operator periodically reviews evaluation records.
2. Disagreements with operator judgement are filed into a tuning queue (a small markdown file `docs/evals/tuning-queue.md`).
3. The harness can run a `prompt-optimize` job (#09 cron candidate, deferred to operator action in v1) that proposes a patch.
4. Operator approves; the patch is applied; tuning log records the change with a new evaluator version.

### Trace emission

- Every LLM call, tool call, validator finding, state transition, and evaluation record produces exactly one JSONL line.
- Lines flow into the session's jsonl (#01).
- For high-volume tools, the harness deduplicates `tool.call` + `tool.result` into a single event with both attributes (optional optimisation; v1 emits two events for clarity).

### Agent-queryable logs and metrics

- Tool `query_logs(filter, since, until)` exposes a LogQL-subset (label matchers + line filter, no joins).
- Tool `query_metrics(metric, since, until, agg)` exposes a PromQL-subset (instant + range queries, `sum`/`avg`/`max` only).
- Both tools are subject to the offload contract (#04); large results are spilled to scratch.

### Tech-debt auto-filing

1. On any verification failure that the harness can pattern-match to a missing capability (e.g. "no fixture for X", "no skill for Y", "no MCP for Z"), the runner appends a tech-debt entry.
2. The pattern matcher is conservative — when in doubt, it files nothing.
3. The entry is committed as part of the session-end commit (#01).

## Acceptance criteria

### Verification (MUST)

1. **MUST** use a browser-driving tool (e.g. Playwright MCP) to verify any UI-affecting change.
2. **MUST** invoke the project's declared test runner for verification of non-UI changes.
3. **MUST** persist verification artefacts (screenshots, logs, traces) under the worktree's sidecars.
4. **MUST** map verification outcomes onto rubric axes for the evaluator (#06).

### Rubric (MUST)

5. **MUST** ship with the four default axes.
6. **MUST** allow per-task weight overrides via the exec-plan ledger.
7. **MUST** record `rubric_version` in every evaluation record.

### Evaluator calibration & tuning (MUST/SHOULD)

8. **MUST** load 3–5 few-shot examples into every evaluator session.
9. **MUST** version-pin calibration examples to the evaluator prompt.
10. **MUST** maintain `docs/evals/tuning-log.md` as append-only.
11. **SHOULD** require operator approval to apply a tuning patch.

### Tracing (MUST)

12. **MUST** emit exactly one JSONL line per LLM call.
13. **MUST** emit exactly one JSONL line per tool call (and one per tool result, in v1).
14. **MUST** emit one JSONL line per validator finding, state transition, and evaluation record.
15. **MUST** align attribute names with OpenTelemetry GenAI conventions where they exist.
16. **MUST** include `span_id` and `parent_span_id` for nestable events.

### Agent-queryable observability (MUST)

17. **MUST** expose `query_logs` and `query_metrics` tools to the agent.
18. **MUST** subject large query results to the #04 offload contract.

### Tech-debt auto-filing (MUST/SHOULD)

19. **MUST** append a tech-debt entry for every pattern-matched verification failure.
20. **SHOULD** keep the matcher conservative — false negatives preferred to false positives.

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
  And no Playwright session is started.

Scenario: Score uses declared rubric weights
  Given a task overrides weights to {functionality: 3, craft: 2, design: 1, originality: 1}
  When the evaluator scores a sprint
  Then weighted_total uses those weights
  And rubric_version is recorded in the evaluation record.

Scenario: Evaluator session loads calibration examples
  Given docs/evals/calibration/ contains 4 scored examples
  When an evaluator session starts
  Then those 4 examples are present in its first prompt
  And the evaluator version pin matches the calibration version.

Scenario: Tuning log appended on patch
  Given operator approves a patch to the evaluator prompt
  When the patch is applied
  Then docs/evals/tuning-log.md gains a new entry
  And the entry records the disagreement summary, diff, and approver.

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
  And if larger than 4 KB it is offloaded per #04.

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

- `test_ui_change_triggers_playwright` — surface routing.
- `test_non_ui_change_uses_project_test_runner` — surface routing.
- `test_playwright_artefacts_written_to_sidecars` — persistence.
- `test_verification_outcomes_mapped_to_rubric_axes` — integration with #06.
- `test_default_rubric_has_four_axes` — defaults.
- `test_per_task_weight_override_honoured` — configurability.
- `test_evaluation_record_includes_rubric_version` — auditability.
- `test_evaluator_session_loads_calibration_examples` — calibration present.
- `test_calibration_examples_version_pinned` — drift mitigation.
- `test_tuning_log_append_only` — file shape.
- `test_tuning_patch_requires_operator_approval` — governance.
- `test_one_event_per_llm_call` — trace granularity.
- `test_one_event_per_tool_call` — trace granularity.
- `test_validator_finding_emitted_as_trace_event` — finding as event.
- `test_state_transition_emitted_as_trace_event` — coverage.
- `test_evaluation_record_emitted_as_trace_event` — coverage.
- `test_trace_attributes_use_otel_genai_names` — naming convention.
- `test_span_nesting_correct` — span_id / parent_span_id semantics.
- `test_query_logs_returns_matching_events` — agent-side query.
- `test_query_metrics_supports_sum_avg_max` — small surface honoured.
- `test_large_query_result_offloaded` — #04 integration.
- `test_verification_failure_files_tech_debt_entry` — auto-filing.
- `test_unmatched_verification_failure_files_nothing` — conservative matcher.
- `test_tech_debt_entry_references_run_artefact` — actionable.
- `test_evaluation_record_persisted_under_docs_evals` — path convention.
- `test_rolling_pass_rate_metric_emitted` — continuous monitoring.

## Edge cases

- **Playwright unavailable** (MCP not on the allowlist or crashes). Verification falls back to non-UI tests, and the runner emits a warning event; the operator is alerted via the session summary.
- **Test runner emits no structured output.** Trace event records exit code only; remediation prompt (#05) can't include pointers; agent must inspect stdout/stderr offload manually.
- **Evaluator's few-shot examples are missing** (file deleted). Evaluator session refuses to start; runner records a blocking finding.
- **Two evaluator sessions disagree** (rare; reviewer + sprint evaluator on same change). Both records are persisted; the operator dashboard surfaces the disagreement.
- **OTel attribute name added by the spec after we shipped.** We add it on next release; existing events are not retroactively rewritten.
- **Agent's `query_logs` returns 0 events** (filter too narrow). Returns empty list; no offload; agent decides next.

## Open questions

- Whether to ship a default rubric for code review-style work (currently the four axes are general; specialised rubrics deferred).
- Whether the tuning loop should auto-suggest patches or remain operator-driven (current: operator-driven).
- Whether to dedupe `tool.call` + `tool.result` into a single event in v1 (current: keep them separate for clarity).
- Whether to support exemplars (failing test plus expected fix) in the calibration set.

## Out of scope

- Specific rubric authoring guidelines (deferred).
- OTel exporter wiring to external backends like Honeycomb or Tempo (→ #11).
- Multi-tenant evaluator pools (deferred).
- The reviewer loop's lightweight verification at session close (→ #03).
- Dashboard / visualisation surfaces for operators (deferred).
