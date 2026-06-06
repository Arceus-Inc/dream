# 06.5 — Background Turns & Heartbeat

**One-liner:** A distinct **turn class** — system-prompted by a tiny `HEARTBEAT.md`, allowed
exactly one legal action (the virtual tool `heartbeat(action: skip|run, tasks, reason)`), and
capped at a single turn — sits between *the schedule fired* and *real work begins*. Cron
triggers (`#07`) and idle wake-ups invoke a background turn; only if it returns `run` does the
runner spawn a normal work session. Heartbeat turns are deliberately invisible to the task
ledger (`#07`), the memory recall view (`#11`), and the orientation ritual (`#03`), and they
honour an **anti-coma forced-run** after `N` consecutive `skip`s so a depressed agent can never
sleep forever.

**Sources (source of truth):** `dream-harness.md` ch. 1 (heartbeat as a virtual-tool decision,
`HEARTBEAT.md` checklist loaded only on background turns, "heartbeat ≠ cron ≠ tasks", anti-coma
guard, "cron jobs can target the next heartbeat instead of firing immediately"). · `#03` (the
turn FSM `read → plan → act → verify → record` is reused unchanged; a background turn is a
*subclass* — same FSM, narrower system prompt, exactly one tool, `max_turns=1`) · `#07` (cron is
the canonical caller — every cron trigger spawns a background turn, **not** a full work
session, and only an explicit `run` decision escalates to a `local_agent` task) · `#11` (memory
recall MUST exclude background-turn records from the prompt-side surface so the heartbeat
remains conversation-independent) · `#04` (background turns bypass compaction — they're single
turns by definition).
**Reference (grounding only, not authority):** This is a new abstraction with no direct
OpenHarness analogue; the closest reference is OpenHarness's `services/cron.py` job dispatch
(currently spawning sessions directly without an intermediate decision turn) — this spec
inserts the missing layer between *trigger fired* and *session spawned*.

---

## Why this matters

The harness so far has two kinds of turns: a **work turn** (orientation done, ledger picked
up, real tools, no upper bound on what it touches) and a **reviewer turn** (Ralph-Wiggum pass,
verification only, no new actions). Both assume a human or a queue has already answered the
question *"is now a good moment to do anything at all?"*. Once the harness starts waking itself
up — on a cron schedule, on an inbound message, on an idle timer — that question stops being
implicit and becomes the load-bearing decision of the entire system.

Two failure modes show up the instant that decision is implicit:

1. **The always-on agent.** If every wake automatically runs a work session, the agent invents
   work to justify having woken. This is the "always finds something to do" anti-pattern from
   `dream-harness.md` ch. 1 — small at first, catastrophic at scale (every wake becomes a
   PR, every PR adds noise to the ledger, every ledger entry triggers more wakes).
2. **The depressive agent.** If the wake decision is unstructured free text, the model can
   indefinitely produce "nothing pressing right now" and the harness silently stops earning
   its keep. Without an anti-coma forced-run, a single bad context window can quiet the system
   for days.

The fix is the same fix `#03` applied to the *outer* loop: pin the state machine, name the
decision, make it a single virtual tool call instead of free text, give it its own tiny system
prompt, and put a guard-rail on it. A **background turn** is that machine. The output of the
turn is a `HeartbeatDecision` (`skip` or `run`) — a single structured value the runner can
branch on without parsing prose. *That* is what cron schedules trigger; the **work session is
downstream of the decision**, not coincident with the schedule.

This also cleanly resolves a confusion latent in `#07`: cron-as-session is correct *for the
work the decision produces*, but the decision itself shouldn't run a full session. Background
turns are the missing intermediate that lets `#07`'s cron stay "just another session" without
making every cron fire a guaranteed expense.

## Scope

**In:** the **background turn** as a turn class (`max_turns=1`, dedicated system prompt, single
permitted tool); the `HEARTBEAT.md` checklist artefact and its loading rules; the
**`heartbeat` virtual tool** schema (`action`, `tasks`, `reason`); the
`HeartbeatDecision` record written to the session jsonl; the **anti-coma forced-run** rule
(`skip` counter, threshold, override-with-`run`-and-empty-`tasks` accounting); the wake
*sources* the background turn handles (cron trigger from `#07`, idle timer, inbound message
from `#10` — sources enumerated, mechanics deferred); the **exclusions** (heartbeat turns are
not in the task ledger, are filtered out of memory recall in `#11`, do not consume the
orientation ritual in `#03`, do not participate in compaction in `#04`); the integration
contract with `#07`'s cron — *cron fires a background turn, not a work session*; the
`heartbeat.decision.{run,skip,forced}` hook events on the `#13` bus.

**Out:** the `heartbeat()` substrate liveness probe / coma detector (a *different* mechanism in
`#03`/`#02` despite sharing the name — see "Two heartbeats" below); the cron registry itself
(`#07`); the autopilot pipeline that consumes the work session a `run` decision spawns (`#09`);
how skills/tools the *work session* eventually loads are discovered (`#06`); the planner
subagent that authors the exec-plans a `run` decision will work on (`#10`); the sandbox tier of
the spawned work session (`#08`); the memory provider implementation that honours the recall
exclusion (`#11`); the actual content of `HEARTBEAT.md` for any given deployment (operator
artefact, not harness code).

## Two heartbeats — disambiguation

The word "heartbeat" appears in two unrelated places in this spec set; conflating them is the
single most common mistake reviewers make.

| Mechanism | Where | Cadence | Purpose | Output |
|---|---|---|---|---|
| **Liveness heartbeat** (`#03`/`#02`) | Inside a long LLM call | every 60 s | Detect substrate coma → trigger failover (`#02`) or session abort | side-effect (cancels the in-flight `__anext__`) |
| **Wake-cycle heartbeat** (this spec) | At session boundaries | per wake source | Decide *whether to start work at all* | a structured `HeartbeatDecision` record |

The liveness heartbeat **runs inside** a turn; the wake-cycle heartbeat **is** a turn. They share
a name because both are the harness asking "is anything alive?" — but the first asks about the
substrate, and the second asks about the work-to-do. Specs `#03` and `#02` own the first; this
spec owns the second.

## Key decisions

1. **A background turn is a `Turn` (in `#03`'s sense), not a `Session`.** It runs the same
   `read → plan → act → verify → record` FSM as any other turn. What distinguishes it is the
   *configuration*: a different system prompt, a different (singleton) tool registry, and a
   hard `max_turns = 1`. Reusing the turn FSM means every observability, checkpointing, and
   crash-resume guarantee `#03` already provides applies to heartbeat turns for free.

2. **The `heartbeat` tool is the only legal action.** The background turn's tool registry
   contains exactly one tool — `heartbeat`. Attempting to call any other tool (or producing a
   final assistant message without calling `heartbeat`) is a turn-level failure recorded as
   `outcome: heartbeat_missing_decision` and forces a `skip` for accounting purposes (but
   does *not* reset the skip counter — see decision 8).

3. **`heartbeat` tool schema (pinned):**
   - `action: "skip" | "run"` — required.
   - `tasks: list[str]` — optional, ignored when `action == "skip"`. Free-form task hints the
     downstream work session will be seeded with; the planner (`#10`) refines them. Hard cap
     `len(tasks) <= 5` to prevent the heartbeat itself from becoming a planning surface.
   - `reason: str` — required, hard cap 200 chars. One line of why. Stored in the decision
     record for trajectory analysis.

4. **`HEARTBEAT.md` is the background-turn system prompt, and *only* the background-turn
   system prompt.** Loaded only when a background turn is constructed; never injected into
   work-turn prompts, never included in recall views (`#11`), never compacted (`#04`).
   Deliberately kept tiny (target ≤ 800 tokens) so the cost of every wake is bounded and
   prompt-cacheable. Lives at `.harness/HEARTBEAT.md` (operator-editable, repo artefact under
   `#01`).

5. **`HeartbeatDecision` is a typed record, not a string.** Written to its own line in the
   session jsonl with `kind: "heartbeat-decision"` and fields
   `{decided_at, action, tasks, reason, skip_streak_before, forced}`. Distinct from
   `TurnRecord` (`#03`) so consumers can filter cheaply.

6. **A `run` decision spawns a work session; a `skip` decision ends the wake.** The runner
   does not loop. One trigger → one background turn → at most one downstream work session.
   This is the rule that keeps cost predictable; without it, a confused model could chain
   heartbeat turns indefinitely.

7. **Anti-coma forced-run.** A persistent counter (`skip_streak`) increments on every `skip`
   decision and resets to 0 on every `run` decision. When `skip_streak >= max_consecutive_skips`
   (default `5`), the *next* background turn is constructed in **forced** mode: the system
   prompt is augmented with the line "Your last N skips have been declined; choose at least
   one task this turn." and the `heartbeat` tool's `action` enum is *narrowed to `["run"]`*
   for that turn only. If the model still produces no tool call, the runner synthesises a
   minimal `run` decision (`reason="forced after N skips"`, `tasks=[]`) and lets the downstream
   work session decide what to do with an empty intent (it will most likely orient and do a
   no-op review). Forced decisions are recorded with `forced=true`.

8. **`heartbeat_missing_decision` is not a `skip`.** A malformed turn (no tool call, wrong
   tool, schema-invalid arguments) does not advance the skip counter — that would let a flaky
   model accidentally trigger an anti-coma forced-run. It is recorded as its own outcome and
   the next wake gets a fresh background turn.

9. **Wake sources are enumerated, not extensible-by-default.** v1 ships three: `cron` (from
   `#07`), `idle_timer` (no work for `T` minutes), `inbound_message` (from `#10`). Each carries
   a typed `WakeSource` discriminator into the background turn for trajectory analysis.
   Plugin-supplied wake sources (`#13`) are out of scope for v1.

10. **Heartbeat turns are invisible to most of the rest of the harness.**
    - **Task ledger (`#07`):** background turns do not create or update exec-plan entries;
      only the downstream work session (if any) does.
    - **Memory recall (`#11`):** `sessions_history` and any recall view filters out
      `kind: "heartbeat-decision"` rows by default; an explicit `--include-heartbeats` flag in
      diagnostic tools surfaces them.
    - **Orientation (`#03`):** background turns skip the orientation ritual entirely (there's
      nothing to orient toward — orientation is what the downstream work session does).
    - **Compaction (`#04`):** non-applicable; a single-turn session never hits a window cap.
    - **Reviewer (`#03`):** the verify sub-state for a background turn checks only that the
      `heartbeat` tool was called with valid arguments — no Ralph-Wiggum pass.

11. **Cron's contract changes from `#07`.** Where `#07` currently reads "a trigger spawns a
    `local_agent` `TaskRecord`", this spec amends it to: *a trigger spawns a background turn;
    a background turn returning `run` spawns a `local_agent` `TaskRecord`*. The `#07` cron
    manifest gains one optional field, `target: "heartbeat" | "session"` (default
    `"heartbeat"`); the `"session"` value preserves the legacy behaviour for cron kinds that
    are pure cleanup (e.g., `reference-refresh`) where a decision turn would be pointless
    overhead.

12. **A `run` decision can target *the next heartbeat* instead of an immediate session.** The
    `tasks` list may contain entries prefixed `defer:` (e.g., `"defer:check whether CI flakes
    cleared up"`); the runner records these as deferred intents in the decision record and
    surfaces them to the next background turn's system prompt as context. This is the
    `dream-harness.md` ch. 1 rule "cron jobs can target the next heartbeat instead of firing
    immediately" recast as something the model itself can do at decision time.

13. **One background turn at a time, per agent.** A file lock (`#08`-style) on
    `.harness/coordination/heartbeat-{agent-id}.lock` prevents two simultaneous wakes from
    racing on the same skip counter. If a second trigger fires while one is in flight, it
    records `wake.dropped(reason: heartbeat_in_flight)` and exits.

14. **`max_consecutive_skips` and `idle_timer_minutes` are per-agent config, not global.** Each
    agent (defined in `#10`'s lane manifests) gets its own thresholds — a curator agent
    should be allowed to skip far longer than a customer-support agent.

## Artefact shapes

### `HEARTBEAT.md` (operator artefact, `.harness/HEARTBEAT.md`)

Sections (suggested template — operator-editable, not enforced):
- **Identity** — one sentence: who this agent is.
- **What counts as "now is a good moment"** — bullet list of conditions; intentionally short.
- **What counts as "no, skip"** — bullet list of conditions; intentionally short.
- **Available tools** — single line: "`heartbeat(action, tasks, reason)`".

Hard rule: no other content in this file should reference work tools, ledger state, or
provider details. The background turn's *only* job is the wake decision.

### `heartbeat` virtual tool (JSON Schema, pinned)

```json
{
  "name": "heartbeat",
  "description": "Decide whether to start work this wake.",
  "input_schema": {
    "type": "object",
    "required": ["action", "reason"],
    "properties": {
      "action": {"enum": ["skip", "run"]},
      "tasks": {
        "type": "array",
        "items": {"type": "string", "maxLength": 200},
        "maxItems": 5
      },
      "reason": {"type": "string", "maxLength": 200}
    }
  }
}
```

### `HeartbeatDecision` jsonl record

```json
{
  "kind": "heartbeat-decision",
  "decided_at": "2026-06-06T14:32:11.4Z",
  "session_id": "hb-2026-06-06-14:32-cron",
  "agent_id": "curator",
  "wake_source": {"kind": "cron", "cron_kind": "doc-garden"},
  "action": "run",
  "tasks": ["refresh stale docs older than 30d", "defer:re-evaluate after CI passes"],
  "reason": "two doc-garden candidates pending",
  "skip_streak_before": 0,
  "forced": false
}
```

### Wake-source discriminators

- `{"kind": "cron", "cron_kind": "doc-garden", "run_id": "..."}`
- `{"kind": "idle_timer", "idle_minutes": 47}`
- `{"kind": "inbound_message", "channel": "...", "message_ref": "..."}`

### Per-agent heartbeat config (subset of `#10` lane manifest)

```toml
[agent.curator.heartbeat]
heartbeat_md = ".harness/HEARTBEAT.curator.md"     # optional override
max_consecutive_skips = 8
idle_timer_minutes = 120
wake_sources = ["cron", "idle_timer"]               # inbound_message off for curator
```

## Behaviours

### Background turn lifecycle (one wake)

1. A wake source fires (`cron` trigger from `#07`, idle timer, inbound message).
2. Runner acquires `heartbeat-{agent-id}.lock`; if held, records `wake.dropped` and exits.
3. Runner constructs a `BackgroundTurnConfig`:
   - system prompt = `HEARTBEAT.md` (+ deferred-intent context from prior decisions, if any).
   - tools = singleton `{heartbeat}`.
   - `max_turns = 1`.
   - `skip_streak` read from persistent state; if `>= max_consecutive_skips`, **forced** mode.
4. Runner spawns a session and drives one turn through the `#03` FSM. Orientation is skipped.
5. The model calls `heartbeat(...)`. The runner records the `HeartbeatDecision`, fires
   `heartbeat.decision.{run,skip,forced}` (`#13`), releases the lock.
6. **If `run`:** runner spawns a `local_agent` `TaskRecord` (`#07`) seeded with `tasks` as
   intent; `skip_streak` resets to 0; deferred intents (prefixed `defer:`) are stored separately
   for the next background turn, not seeded into this session.
7. **If `skip`:** `skip_streak += 1`; no downstream session is created; the wake ends.
8. **If `heartbeat_missing_decision`:** no skip-counter change; logged; the wake ends.
9. **If `forced` and the model still produced no tool call:** runner synthesises a minimal
   `run` decision with `tasks=[]` and proceeds to step 6.

### Cron interaction (revised from `#07`)

1. The OS scheduler fires `harness cron-run {kind}`.
2. Runner loads the manifest; if `enabled=false`, records `cron.skipped` and exits.
3. Runner reads `target` (default `"heartbeat"`):
   - `"heartbeat"` → invoke the **Background turn lifecycle** with
     `wake_source={kind: "cron", cron_kind: kind}`.
   - `"session"` → preserve `#07`'s legacy behaviour: skip the decision turn, spawn the
     `local_agent` `TaskRecord` directly.
4. The cron run-record (`#07`) gains a `heartbeat_decision` field pointing at the
   `HeartbeatDecision` record when `target="heartbeat"`.

### Anti-coma forced-run

1. `skip_streak` is persisted per agent at
   `.harness/coordination/heartbeat-{agent-id}.skip-streak.json`.
2. Read at the start of every background turn under the heartbeat lock; written at the end.
3. When `skip_streak >= max_consecutive_skips`, the next background turn is constructed in
   forced mode (system-prompt addendum + narrowed tool schema).
4. A successful `run` decision resets `skip_streak` to 0.
5. A `heartbeat_missing_decision` outcome does *not* change `skip_streak`.

### Deferred intents

1. A `run` decision whose `tasks` list contains entries prefixed `defer:` is recorded with
   those entries split into a `deferred_intents` field on the `HeartbeatDecision`.
2. Non-deferred entries seed the downstream work session as usual.
3. The runner stores deferred intents at
   `.harness/coordination/heartbeat-{agent-id}.deferred.json` (append-only, bounded to the
   last 20 entries; older entries roll off).
4. The next background turn's system prompt receives them as a "Deferred from prior heartbeats"
   block (read-only context, not authority — the model is free to skip them again or decline
   them entirely).

## Acceptance criteria

### Background turn shape (MUST)

1. **MUST** load `HEARTBEAT.md` as the *only* system-prompt artefact for a background turn
   (no `AGENTS.md`, no orientation brief, no recalled memory).
2. **MUST** expose exactly one tool (`heartbeat`) to the model during a background turn.
3. **MUST** cap a background turn at `max_turns = 1`; a second turn is impossible by
   construction.
4. **MUST** record the result as a `HeartbeatDecision` jsonl entry with `kind: "heartbeat-decision"`.

### `heartbeat` tool semantics (MUST)

5. **MUST** validate the tool input against the pinned schema; schema-invalid inputs are a
   `heartbeat_missing_decision` outcome and **MUST NOT** advance the skip counter.
6. **MUST** treat `tasks` as ignored when `action == "skip"`.
7. **MUST** cap `tasks` at 5 entries and each entry at 200 characters; over-cap → invalid.

### Decision routing (MUST)

8. **MUST** spawn a `local_agent` `TaskRecord` (`#07`) **only** on `action == "run"`.
9. **MUST NOT** spawn more than one downstream work session per background turn.
10. **MUST** terminate the wake immediately on `action == "skip"` with no downstream session.

### Anti-coma (MUST)

11. **MUST** persist the per-agent `skip_streak` to disk between wakes.
12. **MUST** construct the next background turn in **forced** mode when
    `skip_streak >= max_consecutive_skips`.
13. **MUST** narrow the `heartbeat` tool's `action` enum to `["run"]` for forced turns.
14. **MUST** synthesise a minimal `run` decision (`reason="forced after N skips"`, `tasks=[]`)
    if a forced turn still produces no tool call.
15. **MUST** reset `skip_streak` to 0 on any `run` decision (forced or not).
16. **MUST NOT** change `skip_streak` on `heartbeat_missing_decision`.

### Exclusions (MUST)

17. **MUST** skip the orientation ritual (`#03`) for background turns.
18. **MUST NOT** write to the task ledger (`#07`) from a background turn.
19. **MUST** exclude `kind: "heartbeat-decision"` rows from default memory recall views
    (`#11`); diagnostic surfaces SHOULD provide an opt-in to include them.

### Concurrency & coordination (MUST)

20. **MUST** acquire `heartbeat-{agent-id}.lock` before constructing a background turn.
21. **MUST** record `wake.dropped(reason: heartbeat_in_flight)` and exit if the lock is held.
22. **MUST** release the lock before spawning the downstream work session (the lock guards the
    decision, not the work).

### Cron integration (MUST/SHOULD)

23. **MUST** add the `target: "heartbeat" | "session"` field to the cron manifest, defaulting
    to `"heartbeat"`.
24. **MUST** add a `heartbeat_decision` pointer to the `#07` cron run-record when
    `target="heartbeat"`.
25. **SHOULD** preserve `target="session"` as a legitimate operator choice for pure-cleanup
    cron kinds; the decision-turn overhead is not always worth it.

### Hooks (MUST)

26. **MUST** fire `heartbeat.decision.run`, `heartbeat.decision.skip`, or
    `heartbeat.decision.forced` on the `#13` hook bus exactly once per background turn that
    produces a valid decision.
27. **MUST** fire `heartbeat.missing` on the `#13` hook bus exactly once per background turn
    that produces an invalid decision.

### Deferred intents (SHOULD)

28. **SHOULD** parse `defer:`-prefixed entries in `tasks` into a separate `deferred_intents`
    field on the `HeartbeatDecision`.
29. **SHOULD** surface the most recent 20 deferred intents as a read-only context block in the
    next background turn's system prompt.

## Acceptance scenarios

```gherkin
Scenario: Cron trigger fires a background turn, not a work session
  Given the doc-garden cron manifest has target="heartbeat" (default)
  When the OS scheduler fires "harness cron-run doc-garden"
  Then the runner constructs a background turn whose system prompt is HEARTBEAT.md
  And the only tool in the registry is "heartbeat"
  And no local_agent TaskRecord is created until the model calls heartbeat(action="run", ...)

Scenario: Skip ends the wake with no downstream work
  Given a background turn is in flight
  When the model calls heartbeat(action="skip", reason="nothing pending")
  Then a HeartbeatDecision jsonl record is written
  And skip_streak increments by 1
  And no work session is spawned
  And the wake ends.

Scenario: Run with tasks seeds a downstream session
  Given a background turn is in flight
  And skip_streak is 3
  When the model calls heartbeat(action="run", tasks=["refresh docs"], reason="two stale")
  Then a local_agent TaskRecord is created with intent seeded from tasks
  And skip_streak resets to 0.

Scenario: Anti-coma forces a run after N consecutive skips
  Given an agent has skip_streak = max_consecutive_skips
  When the next wake fires
  Then the background turn is constructed in forced mode
  And the heartbeat tool's action enum is narrowed to ["run"]
  And if the model still calls no tool, the runner synthesises a run decision with tasks=[]
  And the HeartbeatDecision record carries forced=true.

Scenario: Malformed heartbeat does not poison the skip counter
  Given a background turn is in flight
  And skip_streak is 2
  When the model produces a final assistant message without calling the heartbeat tool
  Then the outcome is heartbeat_missing_decision
  And skip_streak remains 2
  And heartbeat.missing fires on the hook bus.

Scenario: Concurrent wakes for the same agent are serialised
  Given a background turn for agent "curator" is in flight (lock held)
  When a second wake source fires for the same agent
  Then the second wake records wake.dropped(reason=heartbeat_in_flight)
  And exits without constructing a turn.

Scenario: Heartbeat turns are invisible to memory recall
  Given several past sessions produced HeartbeatDecision records
  When the recall view for the next normal session is assembled
  Then no kind="heartbeat-decision" rows are included by default.

Scenario: Deferred intents surface in the next background turn
  Given a previous heartbeat run included tasks=["defer:check CI tomorrow"]
  When the next background turn is constructed
  Then the system prompt contains a "Deferred from prior heartbeats" block listing that intent
  And the block is read-only context (the model may decline or repeat the defer).

Scenario: Cron manifest with target="session" bypasses the decision turn
  Given reference-refresh.toml sets target="session"
  When that cron fires
  Then no background turn is constructed
  And a local_agent TaskRecord is spawned directly per #07.

Scenario: Heartbeat turn does not run orientation
  Given a background turn is constructed
  When the session FSM advances from starting
  Then orienting is skipped
  And the first (and only) turn enters read directly.
```

## Tests (in test names; bodies belong with the implementation)

- `test_background_turn_loads_only_heartbeat_md` — no AGENTS.md, no orientation brief, no
  recalled memory in the system prompt.
- `test_background_turn_has_only_heartbeat_tool` — registry size is exactly 1.
- `test_background_turn_max_turns_is_one` — second turn raises.
- `test_skip_decision_increments_streak_no_session_spawned` — the skip path.
- `test_run_decision_resets_streak_and_spawns_local_agent_task` — the run path.
- `test_forced_mode_narrows_action_enum_to_run_only` — the schema is dynamic for forced turns.
- `test_forced_mode_synthesises_run_when_model_silent` — the depressed-agent guard.
- `test_malformed_heartbeat_does_not_change_streak` — the flaky-model guard.
- `test_heartbeat_lock_serialises_concurrent_wakes` — second wake is dropped.
- `test_heartbeat_lock_released_before_downstream_session` — work session does not contend
  with the heartbeat lock.
- `test_cron_target_heartbeat_invokes_background_turn` — default cron behaviour.
- `test_cron_target_session_bypasses_decision_turn` — legacy/cleanup escape hatch.
- `test_cron_run_record_links_to_heartbeat_decision` — observability join.
- `test_default_recall_view_excludes_heartbeat_decisions` — `#11` filter applied.
- `test_deferred_intents_surface_in_next_background_turn_prompt` — the `defer:` channel works.
- `test_deferred_intents_bounded_to_last_twenty` — bounded buffer.
- `test_orientation_ritual_skipped_for_background_turns` — FSM short-circuit.
- `test_heartbeat_decision_jsonl_kind_field` — `kind: "heartbeat-decision"` discriminator.

## Edge cases & failure modes

- **Wake source disabled mid-flight.** The wake-source enumeration is per-agent (`#10`); an
  operator disabling `idle_timer` while a wake is in flight does **not** abort the in-flight
  decision. The disable applies to the next trigger.
- **`HEARTBEAT.md` missing.** The background turn fails to start; recorded as a system error
  on the `#13` hook bus (`heartbeat.config_missing`); the wake exits without changing
  `skip_streak`. Operators are expected to see this in `doctor` (`#12`-ish surface, not in this
  spec).
- **Per-agent config absent.** The runner falls back to global defaults
  (`max_consecutive_skips=5`, `idle_timer_minutes=60`); a warning event is emitted once per
  agent per process.
- **Two cron triggers fire within milliseconds.** The heartbeat lock serialises them; the
  second records `wake.dropped` per decision 13. This is *correct*: a wake represents an
  opportunity to decide, not a guaranteed decision; dropping a duplicate opportunity is fine.
- **`run` with empty `tasks` (non-forced).** Legal. The downstream session orients on the
  exec-plan ledger and may legitimately decide to do nothing visible. The skip counter still
  resets, because the *decision* was `run`.
- **Substrate fails during the background turn.** Standard `#03` liveness heartbeat applies
  (the *other* heartbeat); a coma during a background turn aborts the wake with no decision
  recorded, no skip-counter change. The wake source's next firing will retry.

## Open questions (for v1 implementation review)

- **Should `forced` decisions carry a different downstream-session marker?** A forced session
  may want a different sandbox tier or a hard time cap. v1 leans no — forced is recorded on the
  decision but the downstream session is identical to a voluntary `run`. Revisit after first
  long-run telemetry.
- **Should `inbound_message` wakes bypass the heartbeat entirely for trusted channels?** A
  message from the operator's own channel might reasonably go straight to a work session. v1
  leans no — uniform decision discipline; an operator wanting to bypass the heartbeat sends a
  command, not a message.
- **Should `HEARTBEAT.md` support per-wake-source variants?** (`HEARTBEAT.cron.md`,
  `HEARTBEAT.idle.md`.) Plausible, deferred to v2; v1 ships one file per agent.
