# 04 — Context Engineering & Compaction

**One-liner:** Manage the context window deliberately — compact in two tiers (cheap microcompact
before expensive LLM summary) on three triggers (threshold, manual, prompt-too-long), preserve a
fixed contract of fields as typed attachments, reset-and-handoff when compaction stops helping,
offload oversized tool output to disk, disclose skills progressively, and order the prompt so the
substrate serves the stable prefix from cache — all without splitting a single tool-call atom.

**Sources (source of truth):** `docs/specs/new-specs/04-context-engineering.md` — the compaction
trigger/contract, reset-and-handoff, tool-output offload, progressive skill disclosure,
prompt-cache ordering, and the full context-event catalogue are carried forward and enriched ·
`#00` invariant #1 (tool-call atom) + #4 (bounded everything) · `#03` (the turn FSM that calls
compaction at `read`) · `#02` (substrate-reported window size; reactive compaction on provider
errors) · `#06` (skill frontmatter/body) · `#11` (memory tiers between sessions).
**Reference (grounding only, not authority):** [openharness] `services/compact/`
(`CompactTrigger = "auto"|"manual"|"reactive"`, `AutoCompactState`, `auto_compact_if_needed`,
`CompactionResult`, `CompactAttachment`, `estimate_conversation_tokens`,
`_collect_compactable_tool_ids`/`is_microcompactable_tool_result` (microcompact),
`_split_preserving_tool_pairs`/`_boundary_crosses_tool_pair` (atom-safe boundary),
`create_task_focus_attachment_if_needed`, `create_plan_attachment_if_needed`,
`create_recent_files_attachment_if_needed`, `create_invoked_skills_attachment_if_needed`,
`create_recent_verified_work_attachment_if_needed`, `create_compact_boundary_message`,
`build_post_compact_messages`, `_record_compact_checkpoint`, `try_context_collapse`,
`truncate_head_for_ptl_retry`), `engine/query.py` (`_offload_tool_output_if_needed`,
`_bounded_completion_tokens`, the auto/reactive compaction checkpoints in the turn loop),
`engine/stream_events.py` (`CompactProgressEvent`) — used to name the compaction and offload
primitives concretely.

---

## Why this matters

Every long-running harness hits the same wall: the window fills with stale tool output,
superseded plans, and dead-end exploration, and the agent stops making progress. Three patterns
dominate the response — **compact** (summarise older context), **reset** (hand off to a fresh
session when compaction stops helping), **offload** (keep oversized artefacts on disk, quote a
slice). On top of that, *arrangement* matters: prompt caches reward stable prefixes, skill
systems reward progressive disclosure, and tool outputs hide their cost until they blow the
window. This spec makes those patterns boring and automatic so the agent never has to manage its
own context by hand.

The conceptual spec (new-spec 04) defines the policy — *when* to compact, *what* to preserve,
*when* to reset. What it under-specifies, and what OpenHarness's `services/compact/` shows, is the
*mechanism*, and that mechanism sharpens the policy in three ways worth promoting to first-class
decisions:

1. **Three triggers, not one.** Beyond the threshold-based `auto` trigger, there is `manual`
   (operator/agent-initiated) and — critically — **`reactive`**: a provider rejects the request as
   *prompt-too-long* (PTL) mid-turn, and the engine compacts and retries the same call instead of
   failing the turn. The conceptual spec only anticipated proactive threshold compaction; reactive
   compaction is the safety net that keeps a turn alive when the estimate was wrong.

2. **Two tiers, cheap-first.** Before paying for an LLM summary, the engine runs a **microcompact**:
   it drops the *content* of old, safely-droppable tool results (`is_microcompactable_tool_result`)
   while keeping their structural `ToolResultBlock` shells. This often reclaims enough room with
   zero model cost. Full LLM-based summarisation is the fallback when microcompact isn't enough.

3. **The preserved-fields contract is realised as typed attachments.** The conceptual "fields that
   MUST survive verbatim" become `CompactAttachment`s rebuilt after every compaction
   (`create_task_focus_attachment_if_needed`, `create_plan_attachment_if_needed`,
   `create_recent_files_attachment_if_needed`, …). This is what makes the contract *enforceable* —
   the post-compaction message list is *reconstructed* from attachments, not hoped to survive.

And the boundary rule ties straight back to invariant #1: a compaction boundary **must never split
a `tool_use`/`tool_result` pair** (`_boundary_crosses_tool_pair`, `_split_preserving_tool_pairs`).
Compaction is the single most likely place to violate the tool-call atom; this spec forbids it
mechanically.

## Scope

**In:** the three compaction triggers and the two-tier (microcompact → full) flow; the
preserved-fields contract and its attachment realisation; atom-safe compaction boundaries;
token estimation against the substrate window; the reset trigger and structured handoff;
tool-output offload to sidecar scratch; progressive skill frontmatter/body disclosure;
prompt-cache-friendly ordering; the context-operation jsonl events; the PTL-retry path.

**Out:** skill content authoring (`#06`); evaluator/reviewer context budget (`#12`/`#10`); memory
tiers *between* sessions (`#11`); the turn FSM that drives `read`/`act` (`#03`); the compactor and
handoff *prompt templates* (deferred); cross-substrate caching API surfaces (`#02` handles
ordering, no new API here).

## Key decisions

### Triggers and the two-tier flow (enriched from OpenHarness)

1. **Compaction is checked at the start of each turn** (during `read`, `#03`), measuring estimated
   tokens (`estimate_conversation_tokens`) against the substrate-reported window (`#02`). Default
   **auto threshold = 70%** of the window, per-substrate (windows vary). `_bounded_completion_tokens`
   ensures the requested completion never exceeds what the window can hold.

2. **`CompactTrigger` is one of `auto | manual | reactive`.**
   - **auto** — proactive, at the threshold during `read`.
   - **manual** — operator- or agent-initiated.
   - **reactive** — a provider returns a prompt-too-long error mid-call; the engine compacts and
     **retries the same call once** (`try_context_collapse` / `truncate_head_for_ptl_retry`) rather
     than failing the turn. Reactive compaction is attempted **at most once per call**.

3. **Two tiers, cheap-first.** On any trigger, attempt **microcompact** first — drop the *content*
   of old droppable tool results (`is_microcompactable_tool_result`,
   `_collect_compactable_tool_ids`) keeping structural shells — and only escalate to **full
   LLM-based summarisation** if still over threshold.

4. **At most one compaction per turn for the `auto` trigger** (same-turn cooldown). If utilisation
   is still ≥ threshold after compaction, force an immediate turn end (outcome `context-pressure`)
   and proceed to the reset check. (Reactive compaction is orthogonal: it may run on a call inside a
   turn that already auto-compacted, because its job is to rescue an in-flight provider call.)

### The preserved-fields contract (carried + realised as attachments)

5. **These fields MUST survive a compaction verbatim** (the contract, from new-spec 04):
   current exec-plan filename + current step; all `blocked` steps + their `blocked_reason`;
   currently-failing test names (`#12`); file paths modified this task; open plugin hooks/handlers
   (`#13`); the orientation brief in full; the core-beliefs digest; house rules. **Everything else
   MAY be summarised.**

6. **The contract is enforced by reconstruction, not survival.** After compaction the post-compact
   message list is **rebuilt** (`build_post_compact_messages`) from typed `CompactAttachment`s —
   task focus, plan, recent files, invoked skills, recent verified work, work log, hook notes — so
   a contract field is present because it was *re-emitted*, not because it happened to escape the
   summariser. A `create_compact_boundary_message` marks where compaction occurred.

7. **A compaction boundary MUST NOT split a tool-call atom.** Segmenting old-vs-recent messages
   uses `_split_preserving_tool_pairs`; `_boundary_crosses_tool_pair` is the guard. This is
   invariant #1 enforced at the most dangerous site.

8. **Each compaction records a checkpoint** (`_record_compact_checkpoint`) so the pre-compaction
   transcript is recoverable — compaction is loss-permitted but never loss-blind.

### Reset, offload, disclosure, ordering (carried from new-spec 04)

9. **Reset after two consecutive unproductive compactions.** "Unproductive" = a turn that ran a
   compaction but produced no `step → done` ledger transition; a productive turn resets the counter
   to 0. On reaching 2: write a structured handoff to `docs/sessions/handoff/{session-id}.md`, emit
   `context.handoff.written`, and seal the session (`#03`) as `done-handed-off`. The next session
   reads the handoff alongside its orientation brief. (If the *compactor itself* fails on two
   consecutive turns, reset regardless of ledger advancement.)

10. **Offload tool output over the inline limit.** Any single tool result above the configured
    inline size (OpenHarness `tool_output_inline_chars()`; conceptual default 4 KB) is written full
    to `.harness/sidecars/{task-id}/scratch/` and only a preview (head — conceptual head+tail) plus a
    one-line summary is injected, via an offload pointer. The agent pulls more with
    `read_offloaded(path, start, end)`, whose result is itself subject to the offload rule.

11. **Skills disclose progressively.** At session start only frontmatter (`name`, `description`,
    `when_to_use`) is loaded; the body loads on explicit `use_skill(name)` and stays for the session
    unless evicted by compaction (skill bodies are *not* contract fields). Emit `context.skill.loaded`.
    (Authoring + the registry are `#06`.)

12. **Prompt-cache-friendly ordering.** Assemble each turn as
    `[stable preamble][stable tool registry][stable loaded skills][volatile turn input]`; the stable
    portion is byte-stable across turns within a session so the substrate serves it from cache.

13. **Every context operation is a jsonl event**, and the agent can read its own context log
    (`read_my_context_log`) so it can prefer compactable content.

## Artefact shapes (described, not coded)

### `CompactionResult` / `CompactAttachment`

`CompactionResult{messages, did_compact, trigger, …}` — the rebuilt message list plus metadata.
`CompactAttachment{kind, title, lines, metadata?}` rendered into a `ConversationMessage` by
`render_compact_attachment`; the contract fields are emitted as attachments of kinds: task-focus,
plan, recent-files, invoked-skills, recent-verified-work, work-log, hook-note.

### Offload pointer (injected into context)

`offloaded_to` (relative `scratch/` path) · `original_size_bytes` · `head_lines`/`tail_lines`
(or preview char count) · `summary` (one sentence). OpenHarness's inline form is a
`[Tool output truncated]` block naming tool, `tool_use_id`, original size, artifact path, and a
preview.

### Handoff file (`docs/sessions/handoff/{session-id}.md`)

Sections: **Why handing off** · **What was attempted** · **What is still open** · **What is known
not to work** · **Next concrete action**; plus pointers to the sealed jsonl and the final
checkpoint ref (`#01`/`#02`).

### Context jsonl events

`context.compaction.triggered` (utilisation %, trigger) · `context.compaction.completed`
(preserved-attachment count, resulting utilisation %, tier: microcompact|full) ·
`context.reset.triggered` (reason) · `context.handoff.written` (path) ·
`context.tool_output.offloaded` (pointer) · `context.skill.loaded` (name). Progress during a long
compaction is streamed as `CompactProgressEvent` (`#03`).

## Behaviours

### Compaction (per turn, during `read`)

1. Estimate tokens vs substrate window. If ≥ threshold and no `auto` compaction ran this turn:
   emit `context.compaction.triggered`.
2. **Microcompact:** drop droppable old tool-result *content* (atom shells preserved). Re-estimate.
3. If still ≥ threshold: **full compaction** — segment old-vs-recent with
   `_split_preserving_tool_pairs`, summarise the old segment, rebuild via `build_post_compact_messages`
   from contract `CompactAttachment`s + a boundary message; record a compact checkpoint.
4. Emit `context.compaction.completed`. If *still* ≥ threshold, end the turn (`context-pressure`)
   and run the reset check.

### Reactive (PTL) compaction (mid-call)

A provider rejects a call as prompt-too-long → if not already attempted this call: collapse/truncate
context (`try_context_collapse` → `truncate_head_for_ptl_retry`), preserving tool pairs, and retry
the same call once. If the retry also fails PTL → fail the turn with an error record.

### Reset & handoff

After each turn end, update the unproductive-compactions counter; at 2 → write handoff → emit
`context.handoff.written` → seal as `done-handed-off`.

### Tool-output offload

After each tool call, size the result; over the inline limit → write to `scratch/`, inject the
pointer, emit `context.tool_output.offloaded`. `read_offloaded` slices on demand, recursively
offloading large slices.

### Skill disclosure & ordering

Frontmatter at session start; body on `use_skill`; stable-first prompt assembly, byte-stable
within a session.

## Acceptance criteria

### Compaction — triggers & tiers (MUST)

1. **MUST** trigger `auto` compaction at the configured threshold based on the
   substrate-reported window; **MUST** fall back to a configured default window with a warning if
   the substrate reports none.
2. **MUST** attempt microcompact before full LLM summarisation, and **MUST** record which tier ran
   in `context.compaction.completed`.
3. **MUST** support a `reactive` (prompt-too-long) compaction-and-retry, attempted **at most once
   per call**, and fail the turn if the retry still 413/PTL-errors.
4. **MUST** run at most one `auto` compaction per turn and force a turn end (outcome
   `context-pressure`) if still ≥ threshold afterward.
5. **MUST** emit `context.compaction.triggered` and `context.compaction.completed`.

### Preserved-fields contract (MUST)

6. **MUST** preserve every contract field across compaction, realised by reconstructing the
   post-compact message list from typed attachments.
7. **MUST NOT** place a compaction boundary that splits a `tool_use`/`tool_result` pair.
8. **MUST** record a compaction checkpoint so the pre-compaction transcript is recoverable.

### Reset & handoff (MUST)

9. **MUST** reset after 2 consecutive unproductive compactions (or 2 consecutive compactor
   failures), write the handoff with all required sections, and seal as `done-handed-off`.
10. **MUST** make the handoff available to the next session's orientation (`#03`).

### Offload (MUST)

11. **MUST** offload tool results over the inline limit, inject a pointer (not the full result),
    and allow `read_offloaded` slices; **MUST** subject `read_offloaded` results to the same rule.

### Skills, ordering, observability (MUST/SHOULD)

12. **MUST** load only skill frontmatter at session start and the body only on `use_skill`, emitting
    `context.skill.loaded`.
13. **MUST** order the prompt stable-content-first; **SHOULD** keep the stable portion byte-stable
    across turns within a session.
14. **SHOULD** expose context-management events to the agent via `read_my_context_log`.

## Acceptance scenarios

```gherkin
Scenario: Microcompact reclaims room without an LLM call
  Given utilisation is just over threshold and old tool results are droppable
  When auto compaction runs
  Then microcompact drops old tool-result content
  And utilisation falls below threshold
  And no LLM summarisation call is made
  And context.compaction.completed records tier "microcompact".

Scenario: Full compaction rebuilds the contract from attachments
  Given a turn with an exec-plan, two blocked steps, and a failing-test list
  And microcompact did not free enough room
  When full compaction runs
  Then the post-compaction context still contains the exec-plan filename
  And both blocked step entries with their reasons
  And the full failing-test list
  And these arrived as reconstructed CompactAttachments.

Scenario: Compaction boundary never splits a tool pair
  Given the old/recent split would fall between a tool_use and its tool_result
  When the splitter runs
  Then the boundary is moved so the pair stays together
  And the resulting transcript passes provider validation.

Scenario: Reactive compaction rescues a prompt-too-long call
  Given a turn's first provider call returns a prompt-too-long error
  When the engine handles it
  Then context is collapsed/truncated preserving tool pairs
  And the same call is retried once
  And the turn proceeds without being recorded as failed.

Scenario: Reactive compaction does not loop
  Given a call has already been reactively compacted once
  When it returns prompt-too-long again
  Then no second reactive compaction is attempted
  And the turn ends with an error record.

Scenario: Same-turn second auto trigger forces turn end
  Given an auto compaction already ran this turn and utilisation is still above threshold
  Then no second auto compaction runs
  And the turn is recorded with outcome "context-pressure"
  And the reset check runs.

Scenario: Two unproductive compactions trigger reset
  Given the unproductive-compactions counter is 1
  When the next turn compacts and no ledger step transitions to done
  Then the counter reaches 2
  And a handoff file is written
  And the session seals as "done-handed-off".

Scenario: Oversized tool output is offloaded
  Given a tool call returns output over the inline limit
  Then the full output is written under scratch/
  And an offload pointer is injected
  And context.tool_output.offloaded is emitted.

Scenario: Skills load progressively
  Given docs/skills/ contains 12 SKILL.md files
  When the session starts only their frontmatter is in context
  And calling use_skill("foo") loads foo's body and emits context.skill.loaded.

Scenario: Stable prompt prefix is byte-stable across turns
  Given two consecutive turns with no new skills loaded
  Then the stable preamble byte sequence is identical between them.
```

## Tests

- `test_auto_compaction_triggers_at_threshold` / `test_threshold_uses_substrate_window` /
  `test_missing_window_falls_back_with_warning`.
- `test_microcompact_attempted_before_full` / `test_completed_event_records_tier`.
- `test_full_compaction_only_when_microcompact_insufficient`.
- `test_reactive_compaction_retries_ptl_call_once` / `test_reactive_compaction_does_not_loop`.
- `test_only_one_auto_compaction_per_turn` / `test_second_trigger_forces_context_pressure_end`.
- `test_compaction_preserves_exec_plan_pointer` / `_blocked_steps` / `_failing_tests` /
  `_modified_file_paths` / `_orientation_brief` / `_core_beliefs_digest` — contract via attachments.
- `test_post_compact_messages_rebuilt_from_attachments`.
- `test_boundary_never_splits_tool_pair` / `test_compacted_transcript_passes_provider_validation`.
- `test_compaction_records_recoverable_checkpoint`.
- `test_unproductive_counter_increments` / `test_productive_turn_resets_counter` /
  `test_two_unproductive_compactions_trigger_reset` / `test_two_compactor_failures_trigger_reset`.
- `test_reset_writes_handoff_with_required_sections` / `test_reset_seals_done_handed_off` /
  `test_next_session_orientation_reads_handoff`.
- `test_tool_output_under_limit_inline` / `test_over_limit_offloaded` /
  `test_offload_pointer_injected_not_full_result` / `test_read_offloaded_returns_slice` /
  `test_read_offloaded_large_slice_reoffloaded`.
- `test_skill_frontmatter_only_at_session_start` / `test_skill_body_on_use_skill` /
  `test_skill_load_emits_event`.
- `test_prompt_stable_portion_byte_stable_across_turns`.
- `test_read_my_context_log_returns_events`.

## Edge cases

- **Compactor itself fails.** Treated as a tool failure; turn ends with an error record; unproductive
  counter increments; two consecutive compactor failures force a reset regardless of ledger
  advancement.
- **Substrate reports no max window.** Fall back to a configured default and warn.
- **Tool result exactly at the inline limit.** Inlined (threshold is *over* the limit).
- **Offload write fails (disk full).** Return the full result to context, emit an error event, turn
  proceeds with an operator warning.
- **Microcompact would drop a tool result that is part of the *current* unfinished turn.** Not
  droppable — `is_microcompactable_tool_result` only targets settled old results, never the active
  atom.
- **Two skills with the same `name`.** Blocked by the `#01`/`#06` validator at session start; this
  spec consumes only a validated catalogue.
- **Compaction loses a non-contract field the agent valued.** Acceptable — re-fetch from the repo or
  jsonl.

## Open questions

- Whether the auto threshold should be a single number or a band (trigger at 70%, target 50%).
- Whether the offload inline limit should be per-tool configurable (noisy tools benefit from lower).
- Whether skill bodies should be evictable on compaction or treated as semi-stable.
- Whether `auto` and `reactive` should share token estimation or `reactive` should trust the
  provider's error (current: reactive trusts the error and collapses aggressively).

## Out of scope

- Skill content guidelines and authoring (→ `#06`).
- Evaluator/reviewer context budget (→ `#12`, `#10`).
- Memory tiering between sessions (→ `#11`).
- Compactor and handoff prompt templates (deferred).
- Cross-substrate caching API surfaces (→ `#02`).
```
