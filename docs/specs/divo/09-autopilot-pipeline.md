# 09 — Autonomous Repo-Work Pipeline (Autopilot)

**One-liner:** A task does not end when the worker stops typing — it ends when a verified change is
merged. This spec formalises the full sprint-clock loop the other specs only sketch: intake → scored
selection → prepare → run → **verify → open PR → wait for CI → repair → merge**, with a fingerprint
dedup gate at the door, an append-only journal as the only memory, `rebuild_active_context` for
orientation, and label-gated automerge as the governance valve.

**Sources (source of truth):** `docs/specs/new-specs/14-autonomous-repo-work-pipeline.md` — the
richer terminal-aware FSM (`queued → accepted → preparing → running → verifying → pr_open →
waiting_ci → repairing → completed → merged | failed | rejected | superseded`) with the
`completed`-vs-`merged` split, the `fingerprint` intake idempotency key + fold/supersede semantics,
scored selection (`pick_next_card` by `(-score, -updated_at, title.lower())`, re-scored on every
transition), the status-guard single-runner floor, the bounded failure-carrying repair loop, the
append-only journal as sole memory + `rebuild_active_context` orientation, the three CI timers
(`ci_timeout`/`no_checks_grace`/`checks_settle`), the three automerge governance modes
(`pr_only`/`fully_auto`/`label_gated`), `source_kind`+`source_ref` pluggable intake, and the full
acceptance criteria/Gherkin/tests are carried forward verbatim-in-substance and enriched here. That
spec's lineage (OpenHarness autopilot FSM + scored cards + fingerprint dedup + bounded repair +
CI-feedback + label-gated automerge · voyager critique-on-retry · openclaw
schedulers-decide-when/ledger-records-what + 3-then-escalate · paperclip three invariants) is the
conceptual authority. · `#03` (the inner turn loop a `running` card executes) · `#02` (the worktree a
card runs in; teardown on `merged`) · `#08`-the-claim-spec (the status guard is the single-runner
floor; the two-lock lease + heartbeat is the multi-runner upgrade — this spec defines the loop, that
spec defines *who may run it concurrently*) · `#12` (the verifier's rubric + critic isolation, and
the verification-step shape, consumed here) · `#07` (the autopilot card/journal/context are
repo-as-record artefacts; the exec-plan ledger is adjacent) · `#13` (escalation + merge + scan events
ride the hook bus; automerge interacts with governance).
**Reference (grounding only, not authority):** [openharness] `autopilot/types.py`
(`RepoTaskStatus` (the 13-state Literal), `RepoTaskSource` =
`ohmo_request|manual_idea|github_issue|github_pr|claude_code_candidate`, `RepoTaskCard`
(`id`/`fingerprint`/`title`/`body`/`source_kind`/`source_ref`/`status`/`score`/`score_reasons`/
`labels`/`metadata`/`created_at`/`updated_at`), `RepoJournalEntry`
(`timestamp`/`kind`/`summary`/`task_id`/`metadata`), `RepoAutopilotRegistry` (versioned payload with
a `cards` list — the concrete persistence shape), `RepoVerificationStep`
(`command`/`returncode`/`status`/`stdout`/`stderr`), `RepoRunResult`
(`card_id`/`status`/`attempt_count`/`verification_steps`/`worktree_path`/`pr_number`/`pr_url`)),
`autopilot/service.py` (`RepoAutopilotStore` = `list_cards(status=)` sorted by
`(-score, -updated_at, title.lower())`, `enqueue_card` (fingerprint fold + re-score + journal +
`rebuild_active_context`), `pick_next_card`, `update_status` (re-score + journal + rebuild),
`append_journal`, `load_journal`, `load_active_context`/`rebuild_active_context`, `stats`,
`scan_github_issues`/`scan_github_prs`/`scan_claude_code_candidates`/`scan_all_sources`, `run_next`,
`run_card`; policy defaults `max_attempts=3`, `ci_timeout_seconds=1800`, `no_checks_grace_seconds=60`,
`checks_settle_seconds=20`, `dedupe_strategy="source_ref_then_fingerprint"`) — used to name the FSM,
card, journal, store, and CI-timer primitives concretely. This is the one inspiration source with an
end-to-end autonomous PR pipeline in production code, so its naming is followed closely; where the
conceptual spec's defaults differ (e.g. `max_attempts` 4 vs OpenHarness 3) the conceptual spec wins.

---

## Why this matters

Specs `#07` (task engine) and `#12` (verification) each own half of a loop and neither owns the
whole. `#07` takes a task through `pending → in_progress → done | blocked` — an FSM that treats "the
worker finished" and "the change is live" as the same event. `#12` defines what *good* output looks
like (diffs/metrics, critic-sees-outcome-not-code) but stops at the verdict; it never says what the
verdict *does* to the task. The gap between them is exactly where an autonomous company spends most
of its sprint-clock time: a worker produces a diff, the diff fails CI, someone has to look at the
failure, make the *smallest* fix, re-run, and only then merge. That middle stretch — verify, open a
PR, watch CI, repair on red, merge on green — is a state machine of its own, and it is the one
OpenHarness actually shipped and ran.

The conceptual spec (new-spec 14) lifted four lessons from that production pipeline that the other
specs are missing, and this spec carries them forward with their concrete `RepoAutopilotStore`
grounding:

1. **The terminal state is `merged`, not `done`.** A richer FSM makes "the work is integrated" a
   first-class state distinct from "the worker stopped." `completed` = verified, PR open/approved;
   `merged` = integrated. Different events, different owners — the loop reaches `completed`, a gate
   (human or label) reaches `merged`.
2. **Intake needs a dedup gate.** A `fingerprint` over normalised task content collapses the same
   issue arriving via three channels (a GitHub issue, a cron sweep, an operator ask) into one card.
   Without it an autonomous intake loop quietly does the same work N times. OpenHarness's
   `dedupe_strategy="source_ref_then_fingerprint"` makes the precedence concrete.
3. **Selection is scored, not FIFO.** `pick_next_card` sorts by `(-score, -updated_at, title.lower())`
   with the score recomputed on *every* transition (`update_status` re-scores), so the board
   self-prioritises as state changes instead of draining in arrival order.
4. **Repair is bounded and *carries the failure forward*.** Retry is not "run it again"; it is "make
   the **smallest** patch that fixes *this* reported failure, do not restart from scratch, re-run the
   relevant checks" — voyager's critique-on-retry, made operational with `last_failure_stage` +
   `last_failure_summary` threaded into the repair prompt.

The three invariants (paperclip) stay load-bearing: productive work continues (the loop never stalls
on a card it can repair), only real blockers stop it (escalate at the attempt cap, never silently
drop), no infinite loops (`max_attempts`).

## Scope

**In:** the repo-task FSM and its transition rules; the `fingerprint` dedup gate at intake (fold +
supersede); scored card selection (`score` + `score_reasons`, recomputed on transition); the
single-assignee status guard; the bounded repair loop with failure-carrying repair prompts; the
append-only journal (JSONL) + `rebuild_active_context` orientation pattern; CI-feedback handling
(`waiting_ci` with the three timers); the three automerge governance modes; the `RepoAutopilotStore`
operations and the `RepoAutopilotRegistry` persistence shape; `source_kind`/`source_ref` pluggable
intake provenance.

**Out:** the inner turn loop a `running` card executes (→ `#03`); the worktree the card runs in
(→ `#02`, reused as-is); cross-runner claiming/leases/heartbeats (→ `#08`-the-claim-spec) and
runner-death recovery (→ `#10p5` recovery & liveness) — this spec assumes the status guard is the single-runner floor and the
two-lock lease is the multi-runner upgrade); the verifier's grading rubric and critic isolation
(→ `#12`, consumed here); model/provider failover (→ `#02`/`#11`); the intake adapters' transport
details (GitHub API, cron wiring) — `source_kind` is the seam, adapters are `#13` plugin territory.

## Key decisions (assumed defaults)

1. **Richer terminal-aware FSM.** A repo task carries one of: `queued | accepted | preparing |
   running | verifying | pr_open | waiting_ci | repairing | completed | merged | failed | rejected |
   superseded`. The split that matters: `completed` = verified and PR open/approved; `merged` =
   integrated. Different events with different owners.

2. **`fingerprint` is the intake idempotency key.** Computed from normalised task content (title +
   body + source identity), it is the primary dedup key, with `source_ref` consulted first
   (`dedupe_strategy="source_ref_then_fingerprint"`). A new intake whose fingerprint matches a live
   card is folded into that card (re-scored, journalled), not enqueued as a duplicate.

3. **Scored selection, recomputed on every transition.** Each card has a numeric `score` plus
   human-readable `score_reasons`. `pick_next_card` returns the highest
   `(-score, -updated_at, title.lower())` among `queued`. The score is recomputed and the active
   context rebuilt on *every* status change, so the board re-prioritises continuously.

4. **Status guard is the single-runner assignment floor.** A card may not enter `run` while its
   status is in `{preparing, running, verifying, waiting_ci, repairing}` — the in-flight set. This is
   cheap single-process mutual exclusion. **For multi-runner deployments the status guard is
   insufficient and `#08`-the-claim-spec's two-lock lease + heartbeat is required** — this spec
   defines the loop; that spec defines who may run it.

5. **Bounded repair that carries the failure forward.** On a `verifying` or `waiting_ci` failure the
   card goes to `repairing` and re-runs the worker with a repair prompt embedding `last_failure_stage`
   + `last_failure_summary`. The fixed instruction: *"Make the smallest patch that fixes the reported
   failure. Do not restart from scratch if the existing branch already contains valid progress.
   Re-run the relevant verification commands."* Retries bounded by `max_attempts` (default 4); at the
   cap → `failed` + escalate.

6. **Append-only journal is the card's only memory.** Every transition appends one `RepoJournalEntry`
   (JSONL) — never mutates a prior line. The journal is the audit log *and* the recovery source: a
   card's history is replayable from it. No agent's working memory is trusted to track card state.

7. **`rebuild_active_context` on every transition.** After each status change the loop regenerates a
   compact "what's active right now" orientation artefact from the board + journal, so a freshly-woken
   runner (or a human) orients in one read instead of replaying the whole journal. Orientation is
   derived, never hand-maintained.

8. **CI is a feedback signal with three timers, not a blocking wait.** `waiting_ci` polls PR checks
   with: `ci_timeout` (default 1800s — overall budget), `no_checks_grace` (default 60s — how long to
   wait for *any* check before assuming none will appear), `checks_settle` (default 20s — quiet period
   after the last check flips), poll interval 20s. Red → `repairing`; green → `completed`; budget
   exhausted → escalate, never hang.

9. **Automerge has three governance modes.** `pr_only` — open the PR, stop, a human merges (the safe
   default under human gate). `fully_auto` — merge on green, no gate. `label_gated` (recommended
   default) — merge on green **only if** the PR carries a designated label (default `autopilot:merge`),
   so a human grants merge authority per-PR by labelling without performing the merge. The mode is
   policy, set per repo/lane, not per card.

10. **`superseded` is a real terminal state.** When a newer card fingerprints over an older in-flight
    one (re-filed with more detail, requirement changed), the older card is closed `superseded` — not
    `failed` (it did nothing wrong) and not silently dropped (the journal records why).

11. **`source_kind` + `source_ref` make intake pluggable.** A card records where it came from
    (`github_issue`, `github_pr`, `manual_idea`, `ohmo_request`, `claude_code_candidate`, `cron`,
    `operator`, `repair`, …) and an opaque `source_ref`. Intake is a set of adapters that emit cards;
    the FSM downstream is source-agnostic.

## Artefact shapes

### Repo task card (`RepoTaskCard`)

```json
{
  "id": "card_01H...",
  "fingerprint": "sha256:9f2c...",
  "title": "Flaky test in payments suite",
  "body": "test_charge_retry intermittently fails on CI...",
  "source_kind": "github_issue",
  "source_ref": "divo12/Arceus#412",
  "status": "repairing",
  "score": 87,
  "score_reasons": ["labelled priority:high", "blocks CI", "2 duplicate reports folded"],
  "labels": ["bug", "ci", "autopilot:merge"],
  "metadata": { "pr_number": "418", "pr_url": "https://github.com/...", "worktree_path": ".harness/worktrees/card_01H...", "attempt_count": "2", "last_failure_stage": "verify", "last_failure_summary": "pytest exit 1: test_charge_retry assert 3 == 2" },
  "created_at": 1779000000.0,
  "updated_at": 1779003720.0
}
```
Cards are persisted inside a `RepoAutopilotRegistry` (versioned payload with a `cards` list), not as
one file per card. Repair/failure fields live under `metadata` (the reference shape keeps the card
model flat).

### Journal entry (append-only JSONL, `RepoJournalEntry`)

```json
{ "timestamp": 1779003720.0, "kind": "status_repairing", "summary": "verifying → repairing (verify failed: pytest exit 1)", "task_id": "card_01H...", "metadata": { "attempt_count": "2", "stage": "verify" } }
```
One line per transition. Never edited. `kind` ∈ `intake | folded | status_<state> | verification |
ci | pr | merge | escalation | superseded | scan_* | run_failed`.

### Verification step (consumed from `#12`, `RepoVerificationStep`)

```json
{ "command": "pytest tests/payments -q", "returncode": 1, "status": "failed", "stdout": "...", "stderr": "..." }
```

### Run result (the loop's per-attempt output, `RepoRunResult`)

```json
{ "card_id": "card_01H...", "status": "repairing", "attempt_count": 2,
  "verification_steps": [ /* RepoVerificationStep[] */ ],
  "worktree_path": ".harness/worktrees/card_01H...", "pr_number": 418,
  "pr_url": "https://github.com/divo12/Arceus/pull/418" }
```

### Automerge policy (per repo/lane, in standing-orders, `#10`/`#13`)

```json
"automerge": { "mode": "label_gated", "merge_label": "autopilot:merge" }
```

## Behaviours

### Intake (a card arrives — `enqueue_card` / `scan_*`)

1. An intake adapter (a `scan_github_issues`/`scan_github_prs`/`scan_claude_code_candidates` source)
   emits a candidate with title/body/source.
2. Compute `fingerprint` over normalised content; resolve dedup `source_ref`-first then fingerprint.
3. If a **live** card (non-terminal) matches → **fold**: bump its `score`, append a `folded` journal
   entry, `rebuild_active_context`, do *not* enqueue.
4. If a live card is **superseded** by richer content → close the old card `superseded`, enqueue the
   new one.
5. Otherwise enqueue `status=queued`, compute initial `score` + `score_reasons`, journal `intake`,
   `rebuild_active_context`.

### Selection (the loop picks work — `pick_next_card`)

1. `pick_next_card` = highest `(-score, -updated_at, title.lower())` among `queued`.
2. Status-guard check: refuse if the card is in the in-flight set (defensive floor).
3. Transition `queued → accepted → preparing`; journal each; `rebuild_active_context`.
4. (Multi-runner: acquire the `#08` claim before `preparing`; single-runner: the status guard
   suffices.)

### Run → verify (`run_card`)

1. `preparing → running`: create the worktree (`#02`), run the worker turn-loop (`#03`).
2. `running → verifying`: execute the verification steps (`#12`). Critic sees outcomes only.
3. All steps pass → `verifying → pr_open` (open the PR, record `pr_number`/`pr_url` in metadata).
4. Any step fails → set `last_failure_stage=verify` + `last_failure_summary`, go to `repairing`.

### Wait for CI

1. `pr_open → waiting_ci`: poll PR checks every 20s.
2. No check appears within `no_checks_grace` → treat as "no CI configured" → `completed`.
3. A check is red → after `checks_settle`, set `last_failure_stage=ci` + summary → `repairing`.
4. All checks green and settled → `waiting_ci → completed`.
5. `ci_timeout` exhausted with checks still pending → escalate (`#13`), do **not** hang.

### Repair (bounded, failure-carrying)

1. `repairing`: if `attempt_count ≥ max_attempts` → `failed`, journal `escalation`, fire escalation
   hook (`#13`). Stop.
2. Otherwise build the repair prompt embedding `last_failure_stage` + `last_failure_summary` and the
   fixed "smallest-patch, don't-restart, re-run-checks" instruction.
3. `attempt_count += 1`; re-run the worker on the **existing** branch/worktree (preserve prior valid
   progress); back to `running → verifying`.

### Merge (governance valve — `_automerge_eligible`)

1. On `completed`, evaluate against the repo/lane `automerge.mode`:
   - `pr_only` → stop at `completed`; a human merges out-of-band → `merged` on webhook/next poll.
   - `fully_auto` → merge now → `merged`.
   - `label_gated` → merge **iff** the PR carries `merge_label` → `merged`; else hold at `completed`.
2. Journal `merge`; `rebuild_active_context`; tear down the worktree (`#02`).

## Acceptance criteria

### FSM & terminal states (MUST)

1. **MUST** model the full state set incl. distinct `completed` (verified, PR open) and `merged`
   (integrated) states.
2. **MUST** treat `merged`, `failed`, `rejected`, `superseded` as terminal; never re-enter a terminal
   card into selection.
3. **MUST** record `pr_number`/`pr_url` once a PR is opened.

### Intake & dedup (MUST)

4. **MUST** compute a `fingerprint` from normalised content at intake.
5. **MUST** fold a new candidate into a live same-fingerprint card instead of enqueuing a duplicate.
6. **MUST** close an in-flight card `superseded` (not `failed`) when richer content replaces it,
   recording the reason in the journal.
7. **MUST** record `source_kind` + `source_ref` on every card.

### Scored selection (MUST/SHOULD)

8. **MUST** select via `(-score, -updated_at, title.lower())`.
9. **MUST** recompute `score` + `score_reasons` on every status transition.
10. **SHOULD** rebuild the active-context orientation artefact on every transition.

### Single-assignee guard (MUST)

11. **MUST** refuse to `run` a card whose status is in the in-flight set
    `{preparing, running, verifying, waiting_ci, repairing}`.
12. **MUST** defer to `#08`-the-claim-spec's two-lock lease for multi-runner deployments; the status
    guard alone is the single-runner floor only.

### Bounded repair (MUST)

13. **MUST** bound retries by `max_attempts` and escalate (not silently drop) at the cap.
14. **MUST** carry `last_failure_stage` + `last_failure_summary` into the repair prompt.
15. **MUST** instruct the repair worker to make the smallest patch, not restart from scratch, and
    re-run the relevant checks.
16. **MUST** re-run repair on the existing branch/worktree, preserving prior valid progress.

### CI feedback (MUST)

17. **MUST** bound CI waiting by `ci_timeout` and escalate (never hang) on exhaustion.
18. **MUST** treat "no checks within `no_checks_grace`" as no-CI → `completed`.
19. **MUST** wait `checks_settle` after the last check flips before acting on the result.

### Journal & governance (MUST)

20. **MUST** append (never mutate) one journal entry per transition.
21. **MUST** make card state replayable from the journal alone.
22. **MUST** support `pr_only` / `fully_auto` / `label_gated` automerge modes set per repo/lane.
23. **MUST** under `label_gated` merge only when the configured `merge_label` is present, and
    otherwise hold at `completed`.

## Acceptance scenarios

```gherkin
Scenario: Duplicate intake is folded, not re-run
  Given a live card C with fingerprint F at status queued
  When a new candidate arrives with the same fingerprint F
  Then no new card is enqueued
  And C's score increases
  And a "folded" journal entry is appended to C.

Scenario: Selection prefers higher score over arrival order
  Given queued card A (score 40, older) and card B (score 90, newer)
  When the loop picks the next card
  Then it selects B.

Scenario: Score recomputes mid-flight and reorders the board
  Given queued cards A (score 50) and B (score 40)
  When B is labelled priority:high and re-scored to 95 on transition
  Then the next selection returns B, not A.

Scenario: Status guard blocks a second run of an in-flight card
  Given card C is in status running
  When the loop attempts to run C again
  Then the run is refused by the status guard.

Scenario: Verify failure routes to bounded repair carrying the failure
  Given card C reaches verifying and pytest exits non-zero
  Then C goes to repairing with last_failure_stage "verify"
  And the repair prompt contains the failure summary
  And the repair instruction says make the smallest patch and re-run checks.

Scenario: Repair preserves prior progress
  Given card C is repairing on attempt 2 with a branch containing valid progress
  When the repair worker runs
  Then it works on the existing branch
  And does not restart from scratch.

Scenario: Repair cap escalates instead of looping
  Given card C has attempt_count equal to max_attempts and verification fails again
  Then C transitions to failed
  And an escalation journal entry and hook fire
  And C is not retried.

Scenario: CI red triggers repair after settle
  Given card C is waiting_ci and a check turns red
  When checks_settle elapses
  Then C goes to repairing with last_failure_stage "ci".

Scenario: No CI configured completes the card
  Given card C opened a PR and no check appears within no_checks_grace
  Then C transitions to completed without waiting for CI.

Scenario: CI timeout escalates, never hangs
  Given card C is waiting_ci and checks stay pending past ci_timeout
  Then the loop escalates and stops waiting
  And C does not hang in waiting_ci forever.

Scenario: Label-gated automerge holds until labelled
  Given repo automerge mode is label_gated with label autopilot:merge
  And card C is completed with green CI but no merge label
  Then C stays at completed and is not merged
  When the autopilot:merge label is added
  Then C is merged.

Scenario: Newer detailed report supersedes an in-flight card
  Given card C is preparing for issue #412
  When #412 is re-filed with richer detail producing a superseding card
  Then C is closed superseded with a journal reason
  And the new card is enqueued.

Scenario: State is replayable from the journal
  Given card C went queued → ... → merged
  When the board is rebuilt from C's journal alone
  Then C's final status and PR metadata are recovered.
```

## Tests

- `test_fingerprint_dedup_folds_duplicate` — same fingerprint → fold, no new card.
- `test_fingerprint_stable_across_channels` — issue vs cron vs operator collapse to one.
- `test_dedupe_source_ref_first_then_fingerprint` — precedence.
- `test_supersede_closes_old_card_not_failed` — `superseded` terminal, reason journalled.
- `test_pick_next_card_score_order` — `(-score, -updated_at, title)` ordering.
- `test_score_recomputed_on_every_transition` — re-score on status change.
- `test_active_context_rebuilt_on_transition` — orientation regenerated.
- `test_status_guard_blocks_inflight_rerun` — single-assignee floor.
- `test_full_fsm_happy_path_to_merged` — queued → … → merged.
- `test_completed_distinct_from_merged` — two states, two events.
- `test_terminal_cards_not_reselected` — terminal exclusion.
- `test_verify_failure_routes_to_repairing` — failure → repair.
- `test_repair_prompt_carries_failure_stage_and_summary` — critique-on-retry.
- `test_repair_prompt_smallest_patch_instruction` — fixed instruction present.
- `test_repair_reuses_existing_branch` — no restart-from-scratch.
- `test_repair_bounded_by_max_attempts` — cap.
- `test_repair_cap_escalates_to_failed` — escalate not drop.
- `test_ci_red_routes_to_repair_after_settle` — settle window honoured.
- `test_no_checks_grace_completes_card` — no-CI path.
- `test_ci_timeout_escalates_not_hangs` — bounded wait.
- `test_checks_settle_avoids_acting_on_midflight` — settle correctness.
- `test_journal_is_append_only` — no in-place edits.
- `test_state_replayable_from_journal` — recovery from journal alone.
- `test_automerge_pr_only_stops_at_completed` — human gate.
- `test_automerge_fully_auto_merges_on_green` — no gate.
- `test_automerge_label_gated_holds_until_label` — label valve.
- `test_source_kind_and_ref_recorded` — pluggable intake provenance.

## Edge cases

- **Fingerprint collision on genuinely different tasks.** Two distinct issues hash equal (degenerate
  normalisation). Mitigation: include `source_ref` identity in the fingerprint input so same-text
  different-source tasks don't collapse; folding is reversible via the journal if a human spots it.
- **A check flickers red→green within `checks_settle`.** The settle window exists precisely for this:
  act only on the *settled* state, not the transient red.
- **PR merged out-of-band by a human while card is `completed`.** A merge webhook (or the next CI
  poll) flips the card to `merged`; the loop must reconcile external merges, not assume it is the only
  actor.
- **`label_gated` card sits at `completed` forever (label never added).** Acceptable — that *is* the
  human gate. A staleness sweep (`#07` cron) may escalate "completed > N days, unmerged" so it isn't
  silently forgotten.
- **Repair loop fixes verify but breaks CI, then fixes CI but breaks verify (ping-pong).** Bounded by
  `max_attempts` across *both* stages combined, not per-stage — so a card cannot ping-pong
  indefinitely.
- **Worker produces an empty diff (nothing to change).** Treat as `verifying` with a no-op result →
  `completed` with no PR, or `rejected` if the card asserted a change was needed; journal the reason.
- **Multi-runner without `#08`-the-claim-spec.** The status guard alone has a TOCTOU race (two runners
  read `queued`, both proceed). This spec explicitly defers that to the CAS lease — running
  multi-runner on the status guard alone is a known unsafe configuration.

## Open questions

- Whether `score` should be a single number or a vector (priority, staleness, blast-radius) reduced at
  selection — OpenHarness uses a scalar; a vector may prioritise better but is harder to explain in
  `score_reasons`.
- Whether the combined-stage `max_attempts` should split into per-stage budgets (verify vs CI) so a
  card with cheap verify failures isn't starved by expensive CI repairs.
- Whether `rebuild_active_context` should be eager (every transition, matching OpenHarness) or lazy
  (on next runner wake) — eager is simpler, lazy saves writes on bursty boards.
- Whether external-merge reconciliation should poll or rely on webhooks (`#13`) — webhooks are cheaper
  but require an ingress the single-host v1 may not have.
- How `label_gated` interacts with `#13` governance: is the merge label itself a capability-gated
  action (only certain roles may apply it), or any human?

## Out of scope

- The inner turn loop a `running` card executes, and its FSM/flight-recorder (→ `#03`).
- Worktree creation/teardown, checkpoints, atomic writes (→ `#02`; reused as-is).
- Cross-runner claiming, leases, heartbeats (→ `#08`-the-claim-spec) and recovery-on-runner-death
  (→ `#10p5`); this spec defines the loop, those specs define who may run it concurrently and how a
  dead runner is recovered.
- The verifier's grading rubric and critic-isolation discipline (→ `#12`; consumed here).
- Model/provider failover and cooldowns (→ `#02`/`#11`).
- The intake adapters' transport details (GitHub API, cron wiring) — `source_kind` is the seam;
  adapters are implementation (→ `#13` plugins).
