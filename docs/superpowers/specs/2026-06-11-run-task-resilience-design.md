# run_task resilience: self-healing heads + sprint adaptation

Date: 2026-06-11. Status: approved (brainstormed in-session; user approved and
requested implementation).

## Problem

Two failure modes observed live (e2e runs vs gpt-5.2, 2026-06-11):

1. **One-shot parse-strict heads.** All four LLM heads (`planner`,
   `evaluator-run`, `evaluator-propose`, `generator-respond`) make exactly one
   `run_role` call and strictly parse the reply. Any malformed completion
   raises a `*HeadParseError` straight out of `run_task` — one bad completion
   kills the whole task (`PlannerHeadParseError: planner <ledger> is not valid
   JSON` observed killing live runs).
2. **Blind sprint retries.** `apply_outcome` on `needs-changes` flips the step
   back to `in_progress` but **discards the evaluator's notes**, so the
   generator retries with the byte-identical prompt and repeats the identical
   failure (observed: same gate-denied command run in sprint 1 and sprint 2).
   Structural blockers (e.g. permission denials needing an operator) burn the
   entire sprint budget.

## Decisions (user-selected)

- Fix 1 scope: **all four parse-strict heads** via one shared helper.
- Retry shape: **feedback re-prompt** — re-ask the same role with the parse
  error and the previous reply appended; fresh role session per attempt.
- Retry budget: **2 retries (3 attempts total), then re-raise the last parse
  error unchanged** (same exception types as today).
- Fix 2 scope: **notes carry-through + N-strikes** — append evaluator notes to
  `step.notes` on `needs-changes`; after 2 consecutive `needs-changes` on the
  same step, transition it to `blocked` (reusing the existing blocked
  machinery). No string-matching denial heuristics.

## Design

### Fix 1 — `dream/runner/_head_retry.py`

One async helper:

```python
async def ask_until_parsed(ask, parse, *, prompt, retries=2, on_retry=None) -> T
```

- `ask: (prompt) -> Awaitable[RoleResult-like]`, `parse: (final_text) -> T`
  (raises the head's ParseError subclass on bad output).
- Loop: ask → parse; on ParseError build the feedback prompt — original prompt
  + "Your previous reply could not be used: {error}. Your previous reply is
  below. Re-emit your COMPLETE reply with the required envelope, and nothing
  else." + previous reply — and ask again.
- After `retries` failed retries, re-raise the **last** ParseError.
- `on_retry(attempt, error)` callback lets heads emit a `head.retry` observer
  event `{role, attempt, error}` so recoveries are visible, never silent.

All four heads swap their call-then-parse block for this helper. Per-head
parse functions and ParseError types are unchanged (public contract frozen).

### Fix 2 — notes carry-through + N-strikes

- `LedgerStep` (`dream/planner/_artefacts.py`) gains
  `needs_changes_count: int = 0`; `to_dict`/`from_dict` updated; absent key on
  load → 0 (every existing ledger on disk stays readable).
- `apply_outcome` (`dream/sprint/_outcome.py`) on `needs-changes`:
  - increments `needs_changes_count`;
  - appends the evaluator's notes to `step.notes`, labelled
    `[evaluator, sprint {record.sprint_number}] {record.notes}`;
  - if the new count >= `NEEDS_CHANGES_LIMIT` (= 2): transition to `blocked`
    instead of `in_progress`.
- Generator read side: zero change — its prompt already renders `step.notes`
  as a NOTES section.
- Runner (`dream/runner/_run.py`): after applying a `needs-changes` outcome
  that left the step `blocked`, emit `sprint.escalated`
  `{step_id, needs_changes_count}`. `apply_outcome` stays pure.
- Limits are module constants (`retries=2` in `_head_retry.py`,
  `NEEDS_CHANGES_LIMIT = 2` in `_outcome.py`) — no new `run_task` parameters.

## Compatibility

- Exhausted retries raise the same exception types as today.
- Escalation reuses the existing `blocked` status; `run_task` termination
  logic unchanged; public `run_task` signature unchanged.
- Ledger JSON change is additive.

## Testing

- Unit (helper): success-first-try makes exactly one ask; fails-once-then-
  succeeds (feedback prompt contains the error and previous reply); exhaustion
  re-raises the last error; on_retry called with attempt+error.
- Unit (heads): each head recovers from a bad-then-good fake `run_role`.
- Unit (outcome): needs-changes appends labelled notes + increments; second
  needs-changes → blocked; pass/fail behavior unchanged; count-less ledger
  JSON loads as 0.
- Integration (runner): two needs-changes evaluations block the step; observer
  sees `sprint.escalated`.
- Live e2e (standing requirement): re-create the sandbox permission-denial
  scenario — task must block after 2 sprints with the denial in `step.notes`
  instead of burning `max_sprints`; plus a malformed-envelope recovery check.
