# 03 — Loop & Turn Lifecycle

**One-liner:** A session is a state machine: orient → work-turn loop → seal. A turn is: read context → plan → act → verify → record. Every transition fires a hook; nothing else gets to make implicit moves.

**Sources:** [OAI], [ANT-1] · taxonomy §17, §1

---

## Why this matters

The "loop" is the most under-specified part of every harness in the wild. Everyone has one, almost nobody writes down what its states are or what transitions are legal. The result is that you can't reason about half-broken sessions, can't write retries, can't observe progress, and can't share a session between tools.

This spec pins the loop down: explicit states, explicit transitions, deterministic hook points, and a small, opinionated set of rituals (orientation, heartbeat, Ralph-Wiggum review) that we want every session to honour by default. None of this prevents a power user from disabling rituals on a per-task basis; it prevents the default behaviour from being undefined.

## Scope

**In:** session FSM, turn FSM, orientation ritual, heartbeat/coma detection, the reviewer loop, hook-point catalogue for lifecycle events, timeout handling.

**Out:** what the agent actually does in `act` (#05, #06); how context is built before `read` (#04); how verification scores work in detail (#07); the hook bus mechanics themselves (#12).

## Key decisions (assumed defaults)

1. **Session states:** `starting → orienting → working → sealing → done | aborted`. No skipping; no reordering. The only "back" edge is `working → working` for each completed turn.
2. **Turn states:** `read → plan → act → verify → record`. Same shape applies to every turn including the first work turn and the reviewer turns.
3. **Orientation ritual runs exactly once per session.** Before the first work turn the runner: loads `AGENTS.md`, reads `docs/progress.md`, runs the session-start validator (#01), summarises the current exec-plan, and produces an "orientation brief" injected into the first work turn.
4. **Orientation is hybrid.** A deterministic gathering step (file reads, validator) runs first, then a short LLM summary turns the gathered material into the brief. Disable LLM step with a flag for restricted environments.
5. **Heartbeat every 60 seconds.** The runner pings the substrate (#11) during long LLM calls. Three consecutive missed heartbeats → declare the session in a `coma` substate → abort.
6. **Coma is not its own top-level state.** It's a guard-rail attached to `working`. When tripped, the session transitions `working → sealing → aborted` with the abort reason set.
7. **Ralph-Wiggum review.** After the agent says "done," the runner spawns a reviewer subagent (#06). Reviewer either accepts or returns required-changes. The work-turn loop continues until the reviewer accepts or 3 review rounds elapse.
8. **Turn timeout: 10 min hard cap.** If a turn doesn't reach `record` within the cap, the runner aborts *that turn* (writes a synthetic record event with `outcome: timeout`) and continues the session. Three turn-timeouts in a row abort the session.
9. **Every transition fires a hook.** Hook name shape: `session.{from}.to.{to}` and `turn.{from}.to.{to}`. Hooks are observers, not gates — they cannot veto a transition.
10. **First turn always sees the validator findings.** Even warnings the operator might prefer to ignore are surfaced to the agent so it can choose to address them.

## Artefact shapes

### Orientation brief

A small structured object passed into the first turn:
- `repo_summary` — one paragraph from AGENTS.md.
- `progress_tail` — the most recent `progress.md` entry (or summarised top-N if recent activity is dense).
- `active_exec_plan` — pointer to the file plus a one-paragraph summary.
- `validator_findings` — list of `{severity, code, message, path}`.
- `core_beliefs_digest` — bullet list from `core-beliefs.md` (#08).
- `house_rules` — any rules carried in `AGENTS.md`.

### Per-turn record

Recorded into the session jsonl (#01) with these fields:
- `turn_number`
- `started_at`, `ended_at`
- `tools_called` (count + names; full details elsewhere)
- `verification_result` — `pass | fail | skipped`
- `outcome` — `complete | timeout | aborted`
- `notes` — short free-text summary the agent emits on `record`.

## Behaviours

### Session start (orienting)

1. Runner enters `starting`.
2. Runner runs the validator (#01); blocking findings exit non-zero before entering `orienting`.
3. Runner enters `orienting`.
4. Runner deterministically gathers orientation inputs.
5. Runner calls the LLM with a fixed orientation prompt to produce the brief (or skips LLM call if `--no-ai-orientation`).
6. Runner enters `working`.

### Turn

1. `read` — runner assembles context: orientation brief (first turn) + prior turn records + open exec-plan steps + last few jsonl events.
2. `plan` — agent emits a plan-of-action (free-text or structured; harness does not enforce schema here).
3. `act` — agent calls tools, edits files, reads more. May span many tool calls.
4. `verify` — agent (or evaluator subagent, per #07) runs the configured verification.
5. `record` — runner writes the turn record to jsonl, snapshots a checkpoint (#02), fires `turn.complete` hook.

### Heartbeat & coma

- Runner pings substrate every 60 s during any LLM call.
- Each ping either succeeds or returns a transient/hard error (#11).
- Three consecutive transient errors in the same call → coma; runner cancels the LLM call, transitions to `sealing → aborted`, abort reason `coma`.

### Reviewer loop

1. Agent emits `done` (a structured signal).
2. Runner spawns a reviewer subagent with read-only access to the worktree (#06).
3. Reviewer returns `accept` or `request_changes` with a list.
4. If `accept`, runner transitions to `sealing`.
5. If `request_changes`, runner re-enters `working` with the request injected; review-round counter increments.
6. After 3 rounds without accept, runner forces close: writes a "review-failed" record and transitions to `sealing` with status `done-with-warnings`.

### Sealing

1. Runner writes any pending verification artefacts.
2. Runner emits `session.end` into the jsonl.
3. Runner runs the `progress.md` update and commit (#01).
4. Runner fires `session.complete` (or `session.aborted`) hook.

### Turn timeout

- Per-turn wall-clock timer starts at `read`.
- On timeout: runner cancels the active LLM call (if any), writes a synthetic `record` event with `outcome: timeout`, increments consecutive-timeout counter.
- If counter reaches 3, runner transitions session to `aborted` with reason `repeated-timeout`.
- Successful turn resets counter to 0.

## Acceptance criteria

### Session FSM (MUST)

1. **MUST** transition states in the declared order with no skipping.
2. **MUST** refuse to enter `working` if `orienting` did not complete successfully.
3. **MUST** emit a `session.end` jsonl event on every exit path including abort.
4. **MUST** never re-enter `orienting` after the first time within a single session.

### Orientation (MUST)

5. **MUST** run the orientation ritual exactly once per session, before any work turn.
6. **MUST** include validator findings in the orientation brief.
7. **MUST** include the most recent `progress.md` entry in the orientation brief.
8. **MUST** offer a `--no-ai-orientation` mode that skips the LLM summary step.

### Turn FSM (MUST)

9. **MUST** progress every turn through all five sub-states or terminate the turn with a synthetic record.
10. **MUST** record exactly one jsonl turn record per turn, even on timeout.
11. **MUST** snapshot a checkpoint (#02) on every successful turn record.

### Heartbeat & coma (MUST)

12. **MUST** ping the substrate at least every 60 s during long LLM calls.
13. **MUST** abort the session after 3 consecutive heartbeat failures.
14. **MUST** record the abort reason in the final `session.end` event.

### Reviewer loop (MUST/SHOULD)

15. **MUST** spawn at least one reviewer pass before declaring a task `done`.
16. **MUST** cap the reviewer loop at 3 rounds and then force close.
17. **SHOULD** distinguish `done` from `done-with-warnings` based on whether the reviewer accepted.

### Timeouts (MUST)

18. **MUST** abort an individual turn at the configured wall-clock cap.
19. **MUST** reset the consecutive-timeout counter on a successful turn.
20. **MUST** abort the session after the configured consecutive-timeout limit (default 3).

### Hooks (MUST)

21. **MUST** fire a hook on every state transition (session and turn) before continuing.
22. **MUST** continue regardless of hook handler errors; handler errors are logged but never veto a transition.

## Acceptance scenarios

```gherkin
Scenario: Orientation runs once and feeds the first turn
  Given a clean repo passing the validator
  When the session starts
  Then orienting completes before the first work turn
  And the agent's first turn input contains the orientation brief
  And no second orienting transition occurs in the same session.

Scenario: Blocking validator finding aborts at orientation
  Given a session start with a blocking finding from the validator
  When the runner tries to enter orienting
  Then the runner exits non-zero
  And no session jsonl is created beyond a session.start event.

Scenario: Heartbeat miss triggers coma abort
  Given a long-running LLM call in turn 2
  And the substrate fails 3 consecutive heartbeats
  When the runner detects the third failure
  Then the runner cancels the call
  And the session transitions to aborted with reason "coma"
  And the final session.end event records the reason.

Scenario: Reviewer disagreement triggers another round
  Given the agent emits "done" at end of turn 4
  When the reviewer responds with request_changes containing two items
  Then the session re-enters working
  And the review-round counter is 1
  And the next turn's input includes the requested changes.

Scenario: Reviewer max rounds forces close
  Given the reviewer has issued request_changes 3 times
  When the runner receives the third response
  Then the session transitions to sealing
  And the final status is "done-with-warnings"
  And the warnings include the unresolved review items.

Scenario: Turn timeout aborts turn but not session
  Given turn 5 has been running for the configured cap
  When the runner detects the timeout
  Then the runner cancels the active LLM call
  And writes a turn record with outcome "timeout"
  And the session remains in working
  And the consecutive-timeout counter is 1.

Scenario: Three consecutive turn timeouts abort session
  Given the consecutive-timeout counter is 2
  When the next turn also times out
  Then the session transitions to aborted
  And the abort reason is "repeated-timeout".

Scenario: Hook handler error does not veto transition
  Given a registered handler for session.starting.to.orienting that raises
  When the session attempts the transition
  Then the transition completes
  And an error event is logged with the handler name and traceback
  And the session continues normally.

Scenario: Successful turn resets timeout counter
  Given the consecutive-timeout counter is 2
  When the next turn completes within the cap
  Then the counter resets to 0.
```

## Tests

- `test_session_states_traversed_in_order` — FSM order enforced.
- `test_session_cannot_enter_working_without_orientation` — guard.
- `test_session_end_event_emitted_on_success` — sealing.
- `test_session_end_event_emitted_on_abort` — sealing on failure path.
- `test_orientation_runs_once` — no re-entry.
- `test_orientation_brief_contains_validator_findings` — brief content.
- `test_orientation_brief_contains_progress_tail` — brief content.
- `test_orientation_brief_contains_core_beliefs_digest` — brief content.
- `test_no_ai_orientation_skips_llm_call` — restricted-env mode.
- `test_blocking_validator_aborts_before_orientation` — fast fail.
- `test_each_turn_records_exactly_one_jsonl_record` — bookkeeping.
- `test_turn_record_present_on_timeout` — synthetic record on failure.
- `test_checkpoint_snapshot_per_successful_turn` — durability hook (#02).
- `test_heartbeat_fires_during_long_llm_call` — heartbeat happens.
- `test_three_heartbeat_misses_trigger_coma_abort` — coma path.
- `test_coma_abort_records_reason_in_session_end` — auditability.
- `test_done_spawns_reviewer` — Ralph loop kicks in.
- `test_reviewer_request_changes_reopens_working` — loop iterates.
- `test_reviewer_max_rounds_forces_close_with_warnings` — bounded.
- `test_reviewer_accept_seals_session_as_done` — happy path.
- `test_turn_timeout_writes_synthetic_record` — record present.
- `test_turn_timeout_does_not_abort_session_first_time` — session survives.
- `test_three_consecutive_turn_timeouts_abort_session` — session fails fast.
- `test_successful_turn_resets_timeout_counter` — counter resets.
- `test_state_transition_fires_hook` — observability.
- `test_hook_handler_error_does_not_veto_transition` — hooks are observers.
- `test_hook_handler_error_logged_with_traceback` — debuggable.

## Edge cases

- **Agent emits `done` during turn 1.** Allowed; reviewer still spawned.
- **Reviewer itself times out.** Treated as a `request_changes` response with a `reviewer-timeout` item; counts against the round limit.
- **Operator sends SIGTERM mid-turn.** Runner attempts orderly seal (timeout 30 s) then force-exits. Any in-flight work is recorded as `outcome: aborted`.
- **Substrate returns a hard error during heartbeat.** Counts as a heartbeat *failure*, but the underlying call is also cancelled; coma logic still applies.
- **Multiple `done` signals in one turn.** First one wins; subsequent ones logged as info.
- **Session abort during `sealing`.** Runner still attempts to write `session.end`; if even that fails the runner logs to stderr and exits non-zero so the operator can fix the repo.

## Open questions

- Whether the turn timeout should be a wall-clock or a "no token in N seconds" measure (current default: wall-clock).
- Whether to expose a "pause" state for human-in-the-loop intervention (deferred to a later spec).
- Whether reviewer round limit should be operator-configurable per task or fixed at 3.

## Out of scope

- The contents of the orientation prompt (template; deferred).
- Subagent orchestration internals — what tools the reviewer has, etc. (→ #06).
- Hook bus internals (→ #12).
- Verification rubrics (→ #07).
- Context window management within `read` (→ #04).
