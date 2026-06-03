# 06 — Orchestration & Subagents

**One-liner:** Planner expands intent into a spec + ledger. Generator does the work in sprints. Evaluator gates each sprint via a negotiated contract. Subagents communicate through the repo, not RPCs.

**Sources:** [ANT-2], [OAI] · taxonomy §8

---

## Why this matters

Long-running agent tasks fail when a single agent tries to do everything in one undifferentiated stream of LLM calls. You get one of three failure modes:

1. **Plan drift** — the agent keeps inventing the goal as it goes; halfway through, the goal is no longer what the user asked for.
2. **Verification collapse** — the same context that wrote the code is grading it; it scores itself green.
3. **Context bloat** — every concern fights for window space; everything degrades together.

The pattern that survives long-horizon work is decomposing into roles with explicit handoffs:

- **Planner** turns intent into a concrete plan and a tracked ledger.
- **Generator** executes one focused sprint at a time.
- **Evaluator** decides whether the sprint passes.

Communication goes through the repo (committed files), not through in-memory message buses. That makes the whole system inspectable and reproducible — the repo *is* the message log.

## Scope

**In:** the three roles, what each owns, how they hand off, sprint contracts, when to enable/disable each role, depth limits, capability minimisation.

**Out:** the FSM that drives each role's session (→ #03); evaluation rubric content (→ #07); per-worktree isolation (→ #02); cron-spawned subagents (→ #09); the reviewer loop at session close (→ #03 — that's a thin specialisation of the Evaluator pattern).

## Key decisions (assumed defaults)

1. **Three canonical roles:** `planner`, `generator`, `evaluator`. Each runs as a normal harness session (#03) in its own worktree (#02) when needed.
2. **Planner runs once per task** at the very start; produces both a Markdown spec (`docs/exec-plans/active/{task-id}.md`) and a JSON ledger (`docs/exec-plans/active/{task-id}.json`).
3. **Generator works in sprints.** A sprint is the smallest deliverable unit — typically one feature from the ledger.
4. **Before each sprint, generator + evaluator negotiate a sprint contract.** The contract lists acceptance criteria *for this sprint specifically* and is written to `docs/exec-plans/active/{task-id}-sprint-{n}.json` before any code is written.
5. **Evaluator is conditional, not mandatory.** Operators can disable evaluator at task or sprint level. Default policy: enable when task complexity is "near or above" the model's comfort zone; disable for routine work. Complexity heuristic is conservative — when in doubt, leave it on.
6. **Subagent depth capped at 3** — typically planner → generator → reviewer. Deeper trees indicate over-decomposition; the runner refuses to spawn deeper.
7. **Capability minimisation over capability granting.** Each subagent starts with the *smallest* tool/MCP set it needs. The harness derives the minimum from the role's manifest, not from `tier_required` defaults (#05).
8. **Concurrency:** at most 1 generator + 1 evaluator per task; multiple tasks may run in parallel each in its own worktree (#02).
9. **Communication is repo-only.** No in-memory message bus between subagents. Handoffs are files committed to the worktree (or, for the reviewer loop, structured signals captured in the jsonl).
10. **Every handoff is a jsonl event** with from/to roles and a pointer to the artefact (file path or ref).

## Roles in detail

### Planner

- **Input:** the operator's intent (free text), the repo's state, `core-beliefs.md`, `product-specs/`.
- **Output:**
  - `docs/exec-plans/active/{task-id}.md` — narrative plan.
  - `docs/exec-plans/active/{task-id}.json` — ledger (steps + status).
- **Tools:** `read_file`, `git`, `bash` (read-only mode). No write access to anything outside the exec-plan folder.
- **Behaviour:** runs once. If the operator wants to re-plan mid-task, that's a *new task* that consumes the prior plan's state.

### Generator

- **Input:** the ledger, the active sprint contract, the worktree.
- **Output:** code, docs, test changes. Updates the ledger (step transitions).
- **Tools:** full default set (#05) gated by the session's sandbox tier (#08), plus any per-repo additions.
- **Behaviour:** picks the next `pending` step, negotiates a sprint contract with the evaluator (if enabled), executes, marks the step `done` or `blocked`, repeats.

### Evaluator

- **Input:** the sprint contract, the generator's output (the diff), the verification artefacts (#07).
- **Output:** an evaluation record under `docs/evals/{task-id}/sprint-{n}.json` — `pass | fail | needs-changes` + score + notes.
- **Tools:** `read_file`, `git`, `bash` (read-only), verification tools defined in the contract.
- **Behaviour:** runs once per sprint. Does not negotiate further changes — its job is to render judgement; generator decides what to do next.

## Sprint contract shape

JSON file fields:
- `task_id`, `sprint_number`
- `goal` — one sentence
- `scope_includes` — bullet list
- `scope_excludes` — bullet list
- `acceptance_criteria` — bullet list with MUST/SHOULD prefixes
- `verification_steps` — ordered list referring to tests, lints, or evals (#07)
- `evaluator_enabled` — boolean
- `negotiation_log` — append-only list of `{ts, from, to, message}` showing how the contract was reached

## Behaviours

### Task start (planner)

1. Runner allocates `task-id`, creates worktree (#02), starts a session (#03) with role `planner`.
2. Planner reads intent + repo, drafts the spec and ledger.
3. Planner commits both files.
4. Session ends; runner emits `handoff.planner_to_generator` with pointers to both files.

### Generator + evaluator loop (per sprint)

1. Runner starts a generator session in a fresh worktree based on the current state.
2. Generator picks the next `pending` step.
3. If evaluator is enabled for this task:
   - Runner spawns a short evaluator session whose only job is to propose acceptance criteria for the upcoming sprint.
   - Generator and evaluator exchange proposals (≤ 3 rounds) via the `negotiation_log`. Final contract committed.
4. Generator executes the sprint; commits.
5. If evaluator enabled: evaluator runs verification, writes the evaluation record.
6. Based on outcome:
   - `pass` → step marked `done`, loop continues.
   - `needs-changes` → step stays `in_progress`, generator addresses feedback.
   - `fail` → step marked `blocked`, generator escalates (writes a tech-debt entry per #09).

### Disabling the evaluator

- Task-level: a flag on the exec-plan JSON.
- Sprint-level: a flag on the sprint contract; generator can skip the negotiation step and proceed directly to execution.
- The reviewer loop in #03 is *always* on — it's a different mechanism (session-close, lightweight) and is not subject to this evaluator-enabled flag.

### Capability minimisation

For each role the harness computes the minimum tool/MCP set:
- Planner: `read_file`, `git`, `bash --read-only`.
- Generator: defaults (#05) intersected with the session's tier (#08).
- Evaluator: `read_file`, `git`, `bash --read-only`, plus any verification tools the contract names.

Subagents *cannot* expand their toolset mid-session. To gain new tools they must escalate by emitting a structured `request_capability` event; the parent runner decides whether to spawn a new subagent with broader capabilities.

### Depth limit

- Depth 0 = the top-level runner session (the operator's invocation).
- Depth 1 = planner / generator / evaluator.
- Depth 2 = reviewer (#03) or a verification subagent the evaluator spawns.
- Depth 3 = a remediation subagent inside a reviewer.
- Spawning at depth 4 → refused; the parent must inline the work instead.

## Acceptance criteria

### Planner (MUST)

1. **MUST** run exactly once at task start.
2. **MUST** produce both the Markdown spec and the JSON ledger.
3. **MUST** be restricted to read-only access outside the exec-plan folder.
4. **MUST** emit a `handoff.planner_to_generator` event at end of run.

### Generator (MUST)

5. **MUST** pick the next `pending` ledger step at each sprint start.
6. **MUST** transition a step out of `pending` exactly once per sprint.
7. **MUST** record a sprint contract before executing any sprint where the evaluator is enabled.
8. **MUST** never write outside its worktree.

### Evaluator (MUST/SHOULD)

9. **MUST** participate in contract negotiation when enabled, bounded to ≤ 3 rounds.
10. **MUST** produce exactly one evaluation record per sprint when enabled.
11. **MUST** not negotiate after rendering judgement — its output is `pass | fail | needs-changes` with notes.
12. **SHOULD** be disabled by default for explicitly "routine" tasks; enabled otherwise.

### Concurrency & isolation (MUST)

13. **MUST** isolate parallel tasks via worktree (#02).
14. **MUST** allow at most one generator and one evaluator per task at a time.
15. **MUST** refuse to spawn a subagent at depth 4 or deeper.

### Capability minimisation (MUST)

16. **MUST** start each role with its declared minimum toolset.
17. **MUST** refuse mid-session tool expansion.
18. **MUST** allow escalation only via the `request_capability` event, handled by the parent.

### Repo-only communication (MUST)

19. **MUST** route all subagent handoffs through committed files plus jsonl events.
20. **MUST** record each handoff with from-role, to-role, and artefact pointer.

## Acceptance scenarios

```gherkin
Scenario: Planner runs once and produces both artefacts
  Given the operator starts a new task with intent text
  When the planner session completes
  Then docs/exec-plans/active/{task-id}.md exists with narrative content
  And docs/exec-plans/active/{task-id}.json exists with a ledger
  And the planner session jsonl shows exactly one planner.run.completed event.

Scenario: Sprint contract precedes generator code
  Given the evaluator is enabled for task T1
  When generator begins sprint 2
  Then a contract file docs/exec-plans/active/T1-sprint-2.json exists
  And it lists acceptance criteria and verification steps
  And no commit modifying source files in T1's worktree predates the contract.

Scenario: Evaluator can be disabled at task level
  Given the exec-plan JSON sets evaluator_enabled = false
  When generator begins a sprint
  Then no contract negotiation occurs
  And generator proceeds directly to execution
  And no evaluation record is written.

Scenario: Sprint pass marks step done and loop continues
  Given evaluator returns "pass" for sprint 3
  When the runner records the evaluation
  Then the ledger step for sprint 3 moves to "done"
  And generator picks the next pending step in the next sprint.

Scenario: Sprint needs-changes keeps step in progress
  Given evaluator returns "needs-changes" with two items
  When the runner records the evaluation
  Then the step remains in_progress
  And the items appear in the next sprint contract's negotiation log.

Scenario: Sprint fail marks step blocked and files tech debt
  Given evaluator returns "fail" with a reason
  When the runner records the evaluation
  Then the step transitions to "blocked"
  And a tech-debt entry is appended to docs/exec-plans/tech-debt-tracker.md

Scenario: Subagent depth capped at 3
  Given a reviewer at depth 2 attempts to spawn a remediation subagent
  And that subagent attempts to spawn another subagent
  When the deepest spawn is requested
  Then the runner refuses with an "exceeded subagent depth" error
  And the request is recorded as an info event.

Scenario: Mid-session tool expansion refused
  Given a planner running with the read-only toolset
  When the planner attempts to call write_file
  Then the call returns "tool not in role manifest"
  And the planner can emit a request_capability event instead.

Scenario: Parallel tasks isolated via worktrees
  Given two tasks T1 and T2 running concurrently
  When each runs a generator session
  Then the two generators operate in different worktrees (#02)
  And neither sees the other's uncommitted state.

Scenario: Handoff event records artefact pointer
  Given planner has just completed
  When the runner emits handoff.planner_to_generator
  Then the event payload includes the spec path and ledger path
  And the next generator session can locate both files from the payload.
```

## Tests

- `test_planner_runs_once_at_task_start` — single run.
- `test_planner_produces_spec_and_ledger` — both artefacts.
- `test_planner_restricted_to_read_only_outside_exec_plans` — capability minimisation.
- `test_planner_emits_handoff_event` — observability.
- `test_generator_picks_next_pending_step` — selection logic.
- `test_generator_transitions_step_exactly_once_per_sprint` — bookkeeping.
- `test_sprint_contract_written_before_sprint_code` — ordering.
- `test_generator_cannot_write_outside_worktree` — isolation.
- `test_evaluator_negotiation_bounded_at_3_rounds` — bounded.
- `test_evaluator_writes_exactly_one_evaluation_record` — bookkeeping.
- `test_evaluator_does_not_negotiate_after_judgement` — role discipline.
- `test_evaluator_can_be_disabled_at_task_level` — flag honoured.
- `test_evaluator_can_be_disabled_at_sprint_level` — finer flag honoured.
- `test_disabled_evaluator_skips_negotiation_and_record` — full skip.
- `test_pass_outcome_marks_step_done` — outcome mapping.
- `test_needs_changes_keeps_step_in_progress` — outcome mapping.
- `test_fail_outcome_marks_step_blocked` — outcome mapping.
- `test_fail_outcome_files_tech_debt_entry` — escalation.
- `test_subagent_depth_capped_at_3` — depth limit.
- `test_subagent_depth_violation_emits_info_event` — observability.
- `test_role_starts_with_minimum_toolset` — capability minimisation.
- `test_mid_session_tool_expansion_refused` — minimisation enforced.
- `test_request_capability_event_recordable` — escalation path.
- `test_parallel_tasks_use_separate_worktrees` — isolation (#02).
- `test_at_most_one_generator_per_task` — concurrency rule.
- `test_at_most_one_evaluator_per_task` — concurrency rule.
- `test_handoff_event_carries_artefact_pointer` — repo-only comms.
- `test_no_inmem_messaging_between_subagents` — architectural rule.

## Edge cases

- **Operator wants to re-plan mid-task.** Not supported in v1; the operator opens a *new task* whose exec-plan can supersede the prior one. Justification: keeps the planner-runs-once rule simple.
- **Evaluator becomes flaky** (frequent `fail` on actually-correct sprints). Detected by the eval tuning loop (#07), which patches the evaluator's prompt. Not handled here.
- **Generator and evaluator can't agree** on a contract after 3 rounds. Runner picks the *evaluator's* last proposal and proceeds with a warning event; that contract is marked `imposed: true` and the negotiation log shows the disagreement for later review.
- **A step's verification requires a tool the evaluator's minimum set doesn't include.** Contract negotiation must expand the evaluator's toolset for *that sprint only*; spec deferred.
- **Concurrent tasks both touch the same file via separate worktrees.** Resolved at merge/PR-promotion time per task policy (#09); orchestration spec doesn't address merge strategy.
- **A subagent crashes mid-sprint.** The sprint contract remains; the next runner invocation can resume from the last checkpoint (#02) with the contract intact.

## Open questions

- Whether the depth cap should be 3 or 4 (current default: 3; review after first month of real use).
- Whether to allow a "second evaluator" pattern (two evaluators with different rubrics; tie-break by operator).
- Whether contract negotiation should be capped at a smaller number for routine sprints (current: always ≤ 3 rounds).

## Out of scope

- Cron-triggered subagents (→ #09).
- Evaluator rubric content and weights (→ #07).
- Reviewer-loop mechanics at session close (→ #03 — uses these patterns but is governed there).
- Multi-host distribution of subagents (deferred — single-host in v1, like #02).
- A graphical UI for subagent topology (deferred).
