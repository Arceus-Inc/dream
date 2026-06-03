# 03 — Engine: Turn Loop, Streaming & Cost

**One-liner:** A session is a state machine (orient → work-turn loop → seal) and a turn is a
state machine (read → plan → act → verify → record); the engine drives both over a streaming
provider, accumulates cost, fires a hook on every transition, and treats the tool-call atom as
inviolable — so half-broken sessions are reasonable-about, resumable, and observable instead of
undefined.

**Sources (source of truth):** `docs/specs/new-specs/03-loop-and-turn-lifecycle.md` — the
session FSM, turn FSM, orientation ritual, heartbeat/coma detection, reviewer (Ralph-Wiggum)
loop, turn-timeout rules, and lifecycle hook catalogue are carried forward in full and enriched
with the streaming-engine mechanics · `#00` invariant #1 (the tool-call atom) · `#01` (validator
gate, checkpoints) · `#02` (substrate failover the heartbeat triggers) · `#04` (context built
before `read`, compaction) · `#13` (hook bus mechanics).
**Reference (grounding only, not authority):** [openharness] `engine/query_engine.py`
(`QueryEngine`, `submit_message`, `has_pending_continuation`, `QueryContext`), `engine/query.py`
(`run_query` — the act-loop), `engine/messages.py` (`ConversationMessage`; `ContentBlock` =
`TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock`; `sanitize_conversation_messages`;
`assistant_message_from_api`), `engine/stream_events.py` (the `StreamEvent` union:
`AssistantTextDelta`, `AssistantTurnComplete`, `ToolExecutionStarted`, `ToolExecutionCompleted`,
`ErrorEvent`, `StatusEvent`, `CompactProgressEvent`), `engine/cost_tracker.py` (`CostTracker`),
`api/usage.py` (`UsageSnapshot`) — used to name the streaming, transcript, and cost primitives
concretely.

---

## Why this matters

The "loop" is the most under-specified part of every harness in the wild. Everyone has one;
almost nobody writes down its states or its legal transitions. The cost of that omission is
exactly the cost the four-clocks framing (`#00`) warns about: you cannot reason about a
half-broken session, cannot write retries, cannot observe progress, and cannot hand a session
between processes. This spec pins the loop down — explicit states, explicit transitions,
deterministic hook points, and a small set of opinionated rituals (orientation, heartbeat,
reviewer pass) that every session honours by default but a power user can disable per task.

The conceptual spec (new-spec 03) defines *what the states are*. What it leaves abstract — and
what OpenHarness's `engine/` makes concrete — is *how a turn's `act` phase actually runs over a
streaming provider*. The two compose cleanly:

- The **session/turn FSM** is the outer skeleton (new-spec 03, the authority here).
- The **streaming act-loop** (`run_query` driven by `QueryEngine.submit_message`) is what
  executes *inside* the `act` sub-state: it streams assistant text, dispatches tool calls,
  appends results, and re-enters the model until the assistant stops requesting tools. Each
  step surfaces as a `StreamEvent` so the runner can observe progress, fire hooks, and
  accumulate cost without parsing free text.

The single most load-bearing rule the engine must honour is cross-cutting invariant #1: **the
tool-call atom.** A `ToolUseBlock` and its matching `ToolResultBlock` are an indivisible unit.
The engine must never compact between them (`#04`), never inject between them, never checkpoint
a `tool_use` whose `tool_result` is missing (`#01`), and never restore a transcript with a
dangling tool turn. OpenHarness encodes this in `sanitize_conversation_messages` (trims a
trailing `tool_use` that never got its `tool_result`, so a resumed conversation doesn't 400) and
`has_pending_continuation` (detects a transcript ending in tool results that still owe a model
turn — the crash-resume entry point). This spec adopts both as the canonical enforcement points.

## Scope

**In:** the session FSM and turn FSM; the orientation ritual; the streaming act-loop and the
`StreamEvent` contract; the transcript shape (`ConversationMessage`/`ContentBlock`); tool-call
atom enforcement (`sanitize`, `has_pending_continuation`); heartbeat/coma detection (semantics
in `#02`-style failover terms); the reviewer loop; per-turn cost accumulation
(`CostTracker`/`UsageSnapshot`); turn and session timeouts; the lifecycle hook-point catalogue;
the per-turn record written to the session jsonl.

**Out:** what the agent *does* inside `act` — the tool catalogue (`#05`), skills/MCP (`#06`);
how context is assembled before `read` and how compaction works (`#04`); how verification scores
are computed (`#12`); the hook bus implementation (`#13`); the substrate/credential failover
machinery the heartbeat triggers (`#02`); subagent orchestration internals — what tools the
reviewer has (`#06`/`#10`); the orientation/reviewer prompt templates (deferred); cost
forecasting/budgeting (the `UsageSnapshot` stream is the input for a later cost layer).

## Key decisions

### The two state machines (from new-spec 03, verbatim intent)

1. **Session states:** `starting → orienting → working → sealing → done | aborted`. No skipping,
   no reordering; the only back-edge is `working → working` per completed turn. `coma` is a
   guard-rail on `working`, not a top-level state — when tripped it forces `working → sealing →
   aborted` with the reason set.

2. **Turn states:** `read → plan → act → verify → record`. The same five sub-states apply to
   every turn, including the first work turn and each reviewer turn.

3. **Orientation runs exactly once per session**, before the first work turn: load `AGENTS.md`,
   read `docs/progress.md`, run the `#01` validator, summarise the active exec-plan, and produce
   an **orientation brief** injected into the first turn. Orientation is hybrid — a deterministic
   gather step then a short LLM summary; `--no-ai-orientation` skips the LLM step for restricted
   environments.

### The streaming act-loop (enrichment via OpenHarness `engine/`)

4. **The transcript is a typed list of `ConversationMessage`s, never raw strings.** Each message
   is `{role, content: list[ContentBlock]}` where `ContentBlock` is the closed union
   `TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock`. This typing is *what makes the
   tool-call atom mechanically enforceable* — the engine can find dangling `ToolUseBlock`s
   structurally rather than by string-matching.

5. **`act` is the `run_query` loop.** `QueryEngine.submit_message` appends the user message and
   drives `run_query(context, messages)`, which: streams assistant output, and whenever the
   assistant emits `ToolUseBlock`s, dispatches each tool, appends the matching `ToolResultBlock`
   as a user message, and re-enters the model — repeating until the assistant returns no tool
   calls (turn naturally ends) or `max_turns` is hit (bounded — invariant #4). The act-loop is
   bounded by `max_turns`; hitting the cap ends `act` and proceeds to `verify`/`record`, never
   loops forever.

6. **Streaming is a typed event union, not text scraping.** `run_query` yields `StreamEvent`s:
   `AssistantTextDelta` (incremental text), `ToolExecutionStarted`/`ToolExecutionCompleted` (tool
   lifecycle), `AssistantTurnComplete` (carries the turn's `UsageSnapshot`), `StatusEvent`,
   `CompactProgressEvent` (compaction progress — `#04`), and `ErrorEvent`. The runner observes
   these to fire hooks, update the UI, and accumulate cost. **No lifecycle decision is made by
   parsing assistant prose.**

7. **Cost accumulates from the event stream.** A `CostTracker` sums each `AssistantTurnComplete`'s
   `UsageSnapshot` (input/output/cache tokens). The per-turn record carries the turn's usage; the
   session carries the running total. This is the raw input for `#12` observability and any later
   cost layer — this spec emits, it does not budget.

### The tool-call atom (invariant #1 — non-negotiable)

8. **`sanitize_conversation_messages` runs on every restored/continued transcript.** It drops
   legacy empty assistant messages and trims a malformed trailing tool turn (an assistant
   `tool_use` with no matching user `tool_result`), which is exactly the corruption an
   interrupted-mid-turn session leaves behind and which makes OpenAI-compatible providers reject
   the resume. The engine calls it before appending a new user message — never sends an
   unbalanced transcript to a provider.

9. **`has_pending_continuation` is the crash-resume entry point.** A transcript ending in user
   `tool_result`s that still owe a model turn is *not* a finished turn — it is a resumable
   mid-turn checkpoint. On resume (`#01`/`#02`), the engine detects this and re-enters the model
   to consume the pending results rather than treating the turn as complete or discarding work.

10. **Never compact, inject, or checkpoint between a `tool_use` and its `tool_result`.** This is
    the same atom restated at the engine boundary; `#04` (compaction), `#10` (queue drain), and
    `#02` (mid-turn failover) all defer to it.

### Resilience rituals (from new-spec 03)

11. **Heartbeat every 60 s during long LLM calls.** Three consecutive transient failures → coma →
    cancel the call → `sealing → aborted` with reason `coma`. The heartbeat polls `health()`;
    *what failover it triggers* is `#02`'s job, *when to abort* is this spec's.

12. **Reviewer (Ralph-Wiggum) loop before declaring `done`.** When the agent emits the structured
    `done` signal, the runner spawns a read-only reviewer subagent (`#06`/`#10`); `accept` →
    `sealing`; `request_changes` → re-enter `working` with the items injected and increment the
    round counter; after **3 rounds** without accept, force-close as `done-with-warnings` carrying
    the unresolved items.

13. **Turn timeout: 10 min wall-clock hard cap (configurable).** A turn not reaching `record`
    within the cap is aborted *as a turn* (synthetic `record` with `outcome: timeout`), the
    session continues, and a consecutive-timeout counter increments; **3 in a row** aborts the
    session with reason `repeated-timeout`; a successful turn resets the counter.

14. **Every transition fires a hook; hooks are observers, never gates.** Names: `session.{from}.to.{to}`
    and `turn.{from}.to.{to}`. A handler error is logged with traceback but **cannot veto** a
    transition — the session continues regardless.

15. **The first turn always sees the validator findings**, including warnings the operator might
    ignore, so the agent can choose to address them.

## Artefact shapes (described, not coded)

### `ConversationMessage` / `ContentBlock` (transcript)

`ConversationMessage{role: user|assistant, content: list[ContentBlock]}`. `ContentBlock` =
`TextBlock{text}` | `ImageBlock{...}` | `ToolUseBlock{id, name, input}` |
`ToolResultBlock{tool_use_id, content, is_error?}`. Helpers: `.text`, `.tool_uses`,
`.is_effectively_empty()`; `assistant_message_from_api` builds one from a provider response.

### `StreamEvent` union (act-loop output)

`AssistantTextDelta{text}` · `AssistantTurnComplete{usage: UsageSnapshot}` ·
`ToolExecutionStarted{tool, id}` · `ToolExecutionCompleted{tool, id, result}` ·
`StatusEvent{...}` · `CompactProgressEvent{...}` · `ErrorEvent{message, ...}`.

### `QueryContext` (act-loop input)

`{api_client, tool_registry, permission_checker, cwd, model, system_prompt, max_tokens, effort,
context_window_tokens, auto_compact_threshold_tokens, max_turns, permission_prompt,
ask_user_prompt, hook_executor, tool_metadata}` — the engine's bundle of everything `run_query`
needs for one act-loop.

### Orientation brief (first turn)

`repo_summary` · `progress_tail` · `active_exec_plan` (pointer + summary) ·
`validator_findings[]{severity, code, message, path}` · `core_beliefs_digest[]` · `house_rules[]`.

### Per-turn record (session jsonl, `#01`)

`turn_number`, `started_at`, `ended_at`, `tools_called` (count + names),
`verification_result` ∈ {pass|fail|skipped}, `outcome` ∈ {complete|timeout|aborted},
`usage` (this turn's `UsageSnapshot`), `notes` (short agent summary on `record`).

## Behaviours

### Session start (orienting)

`starting` → run `#01` validator (blocking findings exit non-zero before `orienting`) →
`orienting` → deterministic gather → LLM summary (unless `--no-ai-orientation`) → `working`.

### Turn

1. **read** — assemble context: orientation brief (first turn) + prior turn records + open
   exec-plan steps + recent jsonl events. (How the window is sized/compacted is `#04`.)
2. **plan** — agent emits a plan-of-action (free text or structured; no schema enforced here).
3. **act** — `submit_message` runs `run_query`: stream text, dispatch each `ToolUseBlock`, append
   `ToolResultBlock`s, re-enter the model until no tool calls or `max_turns`. Emits `StreamEvent`s
   throughout.
4. **verify** — agent or evaluator subagent runs the configured verification (`#12`).
5. **record** — write the turn record + this turn's usage to jsonl, snapshot a checkpoint (`#02`),
   fire `turn.complete`.

### Heartbeat & coma

Ping `health()` every 60 s during any LLM call; 3 consecutive transient failures → cancel call →
`sealing → aborted` (reason `coma`), recorded in the final `session.end`.

### Reviewer loop

`done` → spawn read-only reviewer → `accept` → `sealing`; `request_changes` → re-enter `working`
(round++ , items injected); 3 rounds → force `sealing` as `done-with-warnings`.

### Sealing

Write pending verification artefacts → emit `session.end` → run the `progress.md` update + commit
(`#01`) → fire `session.complete`/`session.aborted`.

### Crash resume

On restart, load the transcript, run `sanitize_conversation_messages`; if
`has_pending_continuation()` → re-enter the model to consume the owed tool results; else continue
to the next turn. No dangling tool turn ever reaches the provider.

## Acceptance criteria

### Session & turn FSM (MUST)

1. **MUST** traverse session states in declared order with no skipping; **MUST** refuse `working`
   if `orienting` didn't complete; **MUST** never re-enter `orienting` within a session.
2. **MUST** emit a `session.end` jsonl event on every exit path, including abort, with the reason.
3. **MUST** progress every turn through all five sub-states or terminate it with a synthetic
   record; **MUST** write exactly one turn record per turn, even on timeout.
4. **MUST** snapshot a checkpoint (`#02`) on every successful turn record.

### Streaming act-loop (MUST/SHOULD)

5. **MUST** model the transcript as typed `ConversationMessage`/`ContentBlock`, never raw
   strings, so tool-call balance is structurally checkable.
6. **MUST** run the `act` loop as a bounded tool-dispatch loop (`run_query`) capped by `max_turns`;
   hitting the cap ends `act` cleanly, never loops forever.
7. **MUST** surface act progress as typed `StreamEvent`s and make no lifecycle decision by parsing
   assistant prose.
8. **SHOULD** accumulate per-turn `UsageSnapshot` into a `CostTracker` and record per-turn usage in
   the turn record.

### Tool-call atom (MUST)

9. **MUST** run `sanitize_conversation_messages` on every restored/continued transcript before
   sending it to a provider, trimming any trailing unmatched `tool_use`.
10. **MUST** detect a pending continuation (`has_pending_continuation`) on resume and re-enter the
    model rather than treating the turn as complete or discarding the owed results.
11. **MUST NOT** compact, inject, or checkpoint between a `tool_use` and its matching `tool_result`.

### Heartbeat, reviewer, timeouts (MUST/SHOULD)

12. **MUST** ping at least every 60 s during long LLM calls and abort the session after 3
    consecutive heartbeat failures, recording the reason.
13. **MUST** spawn at least one reviewer pass before declaring `done` and cap the reviewer loop at
    3 rounds, then force-close; **SHOULD** distinguish `done` from `done-with-warnings`.
14. **MUST** abort an individual turn at the wall-clock cap, reset the consecutive-timeout counter
    on a successful turn, and abort the session after the configured consecutive-timeout limit
    (default 3).

### Hooks (MUST)

15. **MUST** fire a hook on every session and turn transition before continuing.
16. **MUST** continue regardless of hook-handler errors; errors are logged with traceback and
    never veto a transition.

## Acceptance scenarios

```gherkin
Scenario: Orientation runs once and feeds the first turn
  Given a clean repo passing the validator
  When the session starts
  Then orienting completes before the first work turn
  And the agent's first turn input contains the orientation brief
  And no second orienting transition occurs in the same session.

Scenario: Act-loop dispatches a tool and continues
  Given the model emits one ToolUseBlock in turn 2
  When run_query dispatches the tool
  Then a matching ToolResultBlock is appended as a user message
  And the model is re-entered
  And ToolExecutionStarted and ToolExecutionCompleted events are emitted in order.

Scenario: Act-loop is bounded by max_turns
  Given max_turns is 8 and the model keeps requesting tools
  When the loop reaches the 8th model entry
  Then the act phase ends
  And the turn proceeds to verify and record (no infinite loop).

Scenario: Resume trims a dangling tool_use
  Given a restored transcript ending in an assistant tool_use with no tool_result
  When sanitize_conversation_messages runs
  Then the trailing tool_use message is dropped
  And the provider call does not 400.

Scenario: Resume consumes a pending continuation
  Given a restored transcript ending in user tool_results that owe a model turn
  When the engine resumes
  Then has_pending_continuation returns true
  And the model is re-entered to consume the results
  And the turn is not recorded as complete prematurely.

Scenario: Heartbeat miss triggers coma abort
  Given a long-running LLM call in turn 2
  And health() fails 3 consecutive times
  Then the runner cancels the call
  And the session transitions to aborted with reason "coma"
  And the final session.end records the reason.

Scenario: Reviewer disagreement triggers another round
  Given the agent emits "done" at end of turn 4
  When the reviewer responds request_changes with two items
  Then the session re-enters working with the items injected
  And the review-round counter is 1.

Scenario: Reviewer max rounds forces close
  Given the reviewer has issued request_changes 3 times
  Then the session transitions to sealing
  And the final status is "done-with-warnings" carrying the unresolved items.

Scenario: Turn timeout aborts turn but not session
  Given turn 5 has run for the configured cap
  Then the runner cancels the active LLM call
  And writes a turn record with outcome "timeout"
  And the session remains in working
  And the consecutive-timeout counter is 1.

Scenario: Three consecutive turn timeouts abort session
  Given the consecutive-timeout counter is 2
  When the next turn also times out
  Then the session aborts with reason "repeated-timeout".

Scenario: Hook handler error does not veto transition
  Given a handler for session.starting.to.orienting that raises
  When the transition is attempted
  Then the transition completes
  And an error event is logged with the handler name and traceback.

Scenario: Per-turn usage is recorded
  Given turn 3 completes with an AssistantTurnComplete carrying a UsageSnapshot
  When the runner writes the turn record
  Then the record's usage field reflects that turn's tokens
  And the session running total increases accordingly.
```

## Tests

- `test_session_states_traversed_in_order` / `test_cannot_enter_working_without_orientation` /
  `test_orientation_runs_once`.
- `test_session_end_emitted_on_success` / `_on_abort_with_reason`.
- `test_orientation_brief_contains_validator_findings` / `_progress_tail` / `_core_beliefs_digest`.
- `test_no_ai_orientation_skips_llm_call`.
- `test_blocking_validator_aborts_before_orientation`.
- `test_each_turn_records_exactly_one_jsonl_record` / `test_turn_record_present_on_timeout`.
- `test_checkpoint_snapshot_per_successful_turn`.
- `test_act_loop_dispatches_tool_and_appends_result` — `ToolUseBlock`→`ToolResultBlock` pairing.
- `test_act_loop_emits_tool_execution_events_in_order`.
- `test_act_loop_bounded_by_max_turns` — no infinite loop.
- `test_lifecycle_decisions_never_parse_prose` — events drive the FSM, not text.
- `test_cost_tracker_accumulates_usage` / `test_turn_record_carries_usage`.
- `test_sanitize_trims_trailing_unmatched_tool_use` — resume safety.
- `test_sanitize_drops_empty_assistant_messages`.
- `test_has_pending_continuation_detects_owed_model_turn`.
- `test_resume_consumes_pending_continuation` — re-enters model, not premature complete.
- `test_no_compaction_between_tool_use_and_result` — atom honoured by `#04` boundary.
- `test_heartbeat_fires_during_long_llm_call` / `test_three_heartbeat_misses_trigger_coma_abort` /
  `test_coma_abort_records_reason`.
- `test_done_spawns_reviewer` / `test_reviewer_request_changes_reopens_working` /
  `test_reviewer_max_rounds_forces_close_with_warnings` / `test_reviewer_accept_seals_as_done`.
- `test_turn_timeout_writes_synthetic_record` / `test_turn_timeout_does_not_abort_session_first_time` /
  `test_three_consecutive_turn_timeouts_abort_session` / `test_successful_turn_resets_timeout_counter`.
- `test_state_transition_fires_hook` / `test_hook_handler_error_does_not_veto_transition` /
  `test_hook_handler_error_logged_with_traceback`.

## Edge cases

- **Agent emits `done` during turn 1.** Allowed; reviewer still spawned.
- **Reviewer itself times out.** Treated as `request_changes` with a `reviewer-timeout` item;
  counts against the round limit.
- **Operator sends SIGTERM mid-turn.** Orderly seal attempt (30 s) then force-exit; in-flight work
  recorded as `outcome: aborted`; the transcript is left in a `has_pending_continuation`-resumable
  state where possible.
- **Substrate returns a hard error during heartbeat.** Counts as a heartbeat failure *and* cancels
  the call; coma logic applies.
- **Multiple `done` signals in one turn.** First wins; the rest logged as info.
- **Session abort during `sealing`.** Still attempt `session.end`; if even that fails, log to
  stderr and exit non-zero so the operator can fix the repo.
- **`max_turns` hit mid-tool-batch.** The loop finishes the in-flight tool's `ToolResultBlock`
  (atom) before ending `act` — it never leaves a dangling `tool_use`.
- **Empty assistant message from the provider.** Dropped by `sanitize`/`is_effectively_empty`
  rather than persisted.

## Open questions

- Turn timeout as wall-clock vs "no token in N seconds" (current default: wall-clock; the
  streaming engine measures idle time for the substrate timeout in `#02`, so the two could be
  unified).
- Whether to expose a `pause` state for human-in-the-loop intervention (deferred).
- Whether the reviewer round limit is operator-configurable per task or fixed at 3.
- Whether `max_turns` (act-loop bound) and the turn wall-clock cap should be one knob or two
  (current: two — they bound different things).

## Out of scope

- The contents of the orientation/reviewer prompt templates (deferred).
- The tool catalogue and what `act` can actually call (→ `#05`, `#06`).
- Context-window assembly and compaction inside `read` (→ `#04`).
- Verification rubrics and scoring (→ `#12`).
- The hook bus implementation (→ `#13`).
- Substrate/credential failover machinery the heartbeat triggers (→ `#02`).
- Subagent orchestration internals (→ `#06`, `#10`).
- Cost forecasting and budgeting (the `UsageSnapshot` stream is the input for a later layer).
```
