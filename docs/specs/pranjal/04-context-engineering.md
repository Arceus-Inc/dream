# 04 — Context Engineering

**One-liner:** Manage the context window deliberately: compact when full, reset when broken, offload tool output, disclose skills progressively, order the prompt to maximise cache hits.

**Sources:** [LC], [ANT-2], [AHE] · taxonomy §9, §16, §6

---

## Why this matters

Every long-running harness eventually runs into the same wall: the model's context window fills with stale tool output, deprecated plans, dead-end exploration, and the agent stops making progress. Three patterns dominate the response:

1. **Compaction** — summarise older context so the window keeps room for new work.
2. **Reset** — when compaction stops helping, end the session and hand off cleanly to a fresh one.
3. **Offload** — keep oversized artefacts on disk and quote a small slice in context.

On top of that, the way you *arrange* the context matters. Prompt caches reward stable prefixes; skill systems reward progressive disclosure; tool outputs hide their cost until you blow the window. This spec defines defaults that make these patterns boring and automatic, so the agent doesn't have to remember to manage its own context.

## Scope

**In:** compaction triggers and preserved fields, context-reset triggers and handoff format, tool-output offload, skill frontmatter loading, prompt cache ordering, jsonl events for every context operation.

**Out:** skill content authoring (#05); evaluator-side context (#07); memory tiers (#10); the loop FSM that drives reads (#03).

## Key decisions (assumed defaults)

1. **Compaction trigger:** at 70% of the substrate's reported max context window. Threshold is per-substrate (#11) because windows vary.
2. **Compaction is loss-permitted but contract-bound.** It MUST preserve a fixed set of fields verbatim; everything else MAY be summarised.
3. **Same-turn cooldown:** at most one compaction per turn. A second trigger inside the same turn forces a turn-end rather than a second compaction.
4. **Context reset trigger:** two consecutive compactions without exec-plan advancement. "Advancement" = at least one ledger step transitioning to `done` between the two compactions.
5. **Reset writes a structured handoff file** to `docs/sessions/handoff/{session-id}.md` and ends the current session. The next session reads the handoff as its first input alongside the orientation brief (#03).
6. **Tool output offload threshold:** any single tool result over 4 KB is written full to `.harness/sidecars/{task-id}/scratch/` and only the head (500 lines) + tail (500 lines) + a one-line summary are injected into context.
7. **Skill disclosure:** at session start only skill frontmatter (`name`, `description`, `when_to_use`) is loaded. Full body loaded on explicit `use_skill(name)` call by the agent.
8. **Prompt cache order:** stable preamble first (system prompt, AGENTS.md, core-beliefs, skill frontmatter, tool registry), volatile content last (orientation brief, recent turns, current task state).
9. **Every compaction, reset, and offload is a jsonl event** so the operator can see what happened and the evaluator (#07) can correlate quality regressions with context churn.
10. **The agent can read its own context-management events** during a session — making compaction visible to the agent itself, so it can prefer compactable content.

## Preserved fields on compaction (the contract)

These MUST survive verbatim across a compaction:
- The current exec-plan filename and current step.
- All steps with `status = blocked` and their `blocked_reason`.
- Test names currently failing (per #07).
- File paths the agent has modified in the current task.
- Open hooks/handlers registered by plugins (#12).
- The orientation brief in full.
- Core beliefs digest (#08).
- House rules.

Everything else is fair game for summarisation.

## Artefact shapes

### Offload pointer (injected into context)

A small JSON-shaped block:
- `offloaded_to` — relative path under `scratch/`.
- `original_size_bytes`
- `head_lines` — count of lines retained at start.
- `tail_lines` — count of lines retained at end.
- `summary` — single sentence describing the content.

### Handoff file (`docs/sessions/handoff/{session-id}.md`)

Sections:
- **Why handing off** — one sentence (e.g. "two unproductive compactions").
- **What was attempted** — bullet list pulled from turn records.
- **What is still open** — open ledger steps.
- **What is known not to work** — entries from the tech-debt tracker filed during the session.
- **Next concrete action** — single bullet, ready to execute.
- Pointers to the sealed jsonl and the final checkpoint ref (#02).

### Context jsonl events

Event types added by this spec:
- `context.compaction.triggered` — payload includes utilisation %.
- `context.compaction.completed` — payload includes preserved-field count and resulting utilisation %.
- `context.reset.triggered` — payload includes reason.
- `context.handoff.written` — payload includes the handoff file path.
- `context.tool_output.offloaded` — payload includes the offload pointer.
- `context.skill.loaded` — payload includes skill name.

## Behaviours

### Compaction

1. Each turn, after `read` assembles the context, runner measures token utilisation against substrate's reported window.
2. If utilisation ≥ threshold and no compaction has run this turn, runner:
   - Emits `context.compaction.triggered`.
   - Asks a dedicated compactor (LLM call with the preserved-fields contract baked into its system prompt) to produce a compacted form of the volatile portion.
   - Replaces the volatile portion in context with the compacted form.
   - Emits `context.compaction.completed`.
3. If utilisation is still ≥ threshold after compaction, runner forces an immediate turn end without `act` and proceeds to the reset check.

### Reset

1. After each turn end, runner checks the unproductive-compactions counter:
   - Increment on a turn that ran a compaction and produced no `step → done` ledger transition.
   - Reset to 0 on any turn that does produce a `step → done`.
2. When counter reaches 2:
   - Runner emits `context.reset.triggered`.
   - Runner writes the handoff file.
   - Runner emits `context.handoff.written`.
   - Runner transitions session to `sealing` (#03) with status `done-handed-off`.

### Tool-output offload

1. After every tool call, runner sizes the result.
2. If over 4 KB:
   - Runner writes the full result to `scratch/{tool-call-id}.{ext}`.
   - Runner constructs an offload pointer with head + tail + summary.
   - Runner injects the pointer (not the full result) into the agent's context.
   - Runner emits `context.tool_output.offloaded`.
3. The agent can call `read_offloaded(path, start, end)` to pull more lines on demand; that call is itself a tool call subject to the same offload logic.

### Skill disclosure

1. At session start, runner enumerates `docs/skills/*/SKILL.md`, parses frontmatter, and loads only `name`/`description`/`when_to_use` into context.
2. Agent calls `use_skill(name)` to load the body.
3. On load, runner emits `context.skill.loaded`.
4. Once loaded, the skill body stays in context for the rest of the session unless evicted by compaction (skill bodies are *not* in the preserved-fields contract).

### Prompt-cache-friendly ordering

The runner assembles each turn's prompt as: `[stable preamble][stable tool registry][stable loaded skills][volatile turn input]`. The stable portion is byte-stable across turns within a session — same content, same order — so the substrate (#11) can serve it from cache.

## Acceptance criteria

### Compaction (MUST)

1. **MUST** trigger compaction at the configured threshold based on substrate-reported window size.
2. **MUST** preserve all contract fields verbatim across compaction.
3. **MUST** never run a second compaction in the same turn.
4. **MUST** force turn end if utilisation remains ≥ threshold after compaction.
5. **MUST** emit `context.compaction.triggered` and `context.compaction.completed` events.

### Reset & handoff (MUST)

6. **MUST** trigger a context reset after 2 consecutive unproductive compactions.
7. **MUST** write the handoff file before sealing the session.
8. **MUST** include all five required sections in the handoff file.
9. **MUST** seal the session with status `done-handed-off` after a reset.
10. **MUST** make the handoff file available to the next session's orientation (#03).

### Offload (MUST)

11. **MUST** offload tool results larger than 4 KB.
12. **MUST** keep head + tail of the configured sizes in context.
13. **MUST** inject an offload pointer instead of the full result.
14. **MUST** allow the agent to request additional slices from the offloaded file.
15. **MUST** subject `read_offloaded` results to the same offload rule.

### Skills (MUST)

16. **MUST** load only frontmatter at session start.
17. **MUST** load skill body only on explicit `use_skill` call.
18. **MUST** emit a `context.skill.loaded` event on body load.

### Ordering & caching (MUST/SHOULD)

19. **MUST** order prompt with stable content first.
20. **SHOULD** keep the stable portion byte-stable across turns within a session.

### Observability (SHOULD)

21. **SHOULD** make context-management events visible to the agent itself via a `read_my_context_log` tool.

## Acceptance scenarios

```gherkin
Scenario: Compaction triggers at the configured threshold
  Given the substrate reports a max window of 200_000 tokens
  And the threshold is 70%
  When the assembled context reaches 140_001 tokens
  Then a compaction.triggered event is emitted
  And a compaction.completed event is emitted in the same turn
  And the next assembled context is below threshold.

Scenario: Compaction preserves the contract fields
  Given a turn with an exec-plan, two blocked steps, and a failing test list
  When compaction runs
  Then the post-compaction context still contains the exec-plan filename
  And both blocked step entries
  And the full failing-test list.

Scenario: Same-turn second trigger forces turn end
  Given compaction has already run this turn
  And utilisation is still above threshold
  When the runner checks utilisation again
  Then no second compaction runs
  And the runner records the turn with outcome "context-pressure"
  And proceeds to the reset check.

Scenario: Two unproductive compactions trigger reset
  Given a session with the unproductive-compactions counter at 1
  When the next turn runs a compaction and no ledger step transitions to done
  Then the counter reaches 2
  And the runner triggers a context reset
  And writes the handoff file
  And seals the session as "done-handed-off".

Scenario: Productive turn resets the unproductive counter
  Given the counter is 1
  When a turn runs a compaction and transitions a ledger step to done
  Then the counter resets to 0.

Scenario: Oversized tool output is offloaded
  Given a bash tool call returns 12 KB of stdout
  When the runner processes the result
  Then a file is written under scratch/
  And the offload pointer is injected into context
  And a context.tool_output.offloaded event is emitted.

Scenario: Agent requests more lines from offloaded file
  Given an earlier offload pointer for scratch/abc.txt
  When the agent calls read_offloaded(scratch/abc.txt, 600, 700)
  Then the requested slice is returned
  And if the returned slice exceeds 4 KB it is itself offloaded.

Scenario: Skills load progressively
  Given docs/skills/ contains 12 SKILL.md files
  When the session starts
  Then only frontmatter for all 12 is in context
  When the agent calls use_skill("foo")
  Then the body of foo is loaded
  And a context.skill.loaded event is emitted.

Scenario: Prompt stable portion is byte-stable across turns
  Given two consecutive turns in the same session with no new skills loaded
  When the runner assembles each turn's prompt
  Then the stable preamble byte sequence is identical between the two turns.
```

## Tests

- `test_compaction_triggers_at_threshold` — basic trigger.
- `test_compaction_threshold_uses_substrate_window` — substrate-aware.
- `test_compaction_preserves_exec_plan_pointer` — contract field.
- `test_compaction_preserves_blocked_steps` — contract field.
- `test_compaction_preserves_failing_tests` — contract field.
- `test_compaction_preserves_modified_file_paths` — contract field.
- `test_compaction_preserves_orientation_brief` — contract field.
- `test_compaction_preserves_core_beliefs_digest` — contract field.
- `test_only_one_compaction_per_turn` — cooldown.
- `test_second_trigger_forces_turn_end` — pressure release.
- `test_compaction_events_emitted` — observability.
- `test_unproductive_compactions_counter_increments` — counter logic.
- `test_productive_turn_resets_counter` — counter reset.
- `test_two_unproductive_compactions_trigger_reset` — reset trigger.
- `test_reset_writes_handoff_file` — artefact written.
- `test_handoff_contains_required_sections` — handoff format.
- `test_reset_seals_session_as_done_handed_off` — session status.
- `test_next_session_orientation_reads_handoff` — handoff consumed.
- `test_tool_output_under_threshold_inline` — small outputs not offloaded.
- `test_tool_output_over_threshold_offloaded` — offload path.
- `test_offload_keeps_configured_head_and_tail` — slice correct.
- `test_offload_pointer_injected_not_full_result` — context kept lean.
- `test_read_offloaded_returns_slice` — slice retrieval.
- `test_read_offloaded_result_itself_offloaded_if_large` — recursive rule.
- `test_skill_frontmatter_only_at_session_start` — disclosure.
- `test_skill_body_loaded_on_use_skill` — explicit load.
- `test_skill_body_load_emits_event` — observability.
- `test_prompt_stable_portion_byte_stable_across_turns` — cache friendliness.
- `test_read_my_context_log_returns_events` — agent self-introspection.

## Edge cases

- **Compactor itself fails.** Treated as a tool failure; turn ends with an error record; unproductive counter increments. If the compactor fails on two consecutive turns, the runner triggers a reset regardless of ledger advancement.
- **Substrate reports no max window.** Runner falls back to a configured default (e.g. 100k tokens) and emits a warning.
- **Tool result is exactly 4 KB.** Inlined (threshold is *over* 4 KB).
- **Offload write fails (disk full).** Runner returns the full result to context and emits an error event; the turn proceeds but the operator gets a warning.
- **Two skills with the same `name`.** Validator (#01) blocks at session start; this spec only consumes the validated catalogue.
- **A skill's `when_to_use` is empty.** Loaded with empty value; agent decides whether to use.
- **Compaction reduces context but loses a non-contract field the agent thought was important.** Acceptable; the agent can re-fetch from the repo or jsonl.

## Open questions

- Whether the compaction threshold should be a single number or a band (e.g. trigger at 70%, target 50%).
- Whether the offload threshold should be operator-configurable per tool (some tools are noisy and benefit from a lower threshold).
- Whether skill bodies should be evictable on compaction or treated as semi-stable.

## Out of scope

- Skill content guidelines and authoring (deferred).
- Evaluator/reviewer context budget (→ #07 + #06).
- Memory tiering between sessions (→ #10).
- Compactor prompt template (deferred).
- Cross-substrate caching APIs (→ #11).
