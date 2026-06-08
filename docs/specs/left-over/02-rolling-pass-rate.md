# 02 — Rolling pass-rate metric (left over from #12e)

**Deferred from:** `#12` slice 12e (the tech-debt half shipped; this is the other half).
**Status:** not built — blocked on `#12d`.

## What was deferred

Spec 12 decision #12 / AC #22: *"Rolling pass-rate is the continuous-monitoring
metric (per axis / per task / per session), surfaced to the dream phase (`#11`)
as a quality signal."* 12e shipped the **tech-debt auto-filing** half
(`verification/_tech_debt.py`); the rolling pass-rate is this left-over.

## Why it was deferred

The spec defines pass-rate **per rubric axis and per evaluation outcome**
(`pass | needs-changes | fail`). Those outcomes are produced by the **evaluator**
(`#12d`), which does not exist yet. There is also no historical store of
evaluation records to compute a *rolling* rate over. Building a pass-rate now
would either (a) invent a verification-only pass/fail metric that competes with
the spec's rubric-based one, or (b) need a record store with no producer.

## Scope (when built)

- Read evaluation records (`docs/evals/{task-id}/sprint-{n}.json`, `#12d`) over a
  window and compute pass-rate per **axis**, per **task**, and per **session**.
- Surface it to the dream phase (`#11`) as a consolidation signal, and (later)
  to an operator dashboard (out of scope here).
- Likely reuses the `#12b` `query_metrics` shape (aggregate over a derived
  metric) once `evaluation.record` events carry the outcome.

## Acceptance criteria

1. **SHOULD** emit a rolling pass-rate per axis / task / session from evaluation
   records.
2. **MUST** define pass-rate from the rubric outcome (`#12d`), not from raw
   verification pass/fail.
3. **SHOULD** be consumable by the `#11` dream phase.

## Dependencies

- `#12d` evaluation records (rubric outcomes) — the producer. **Blocking.**
- `#12a` `evaluation.record` trace event (schema shipped) / `#12b` query tools —
  reusable for surfacing.
- `#11` dream phase — the consumer.
