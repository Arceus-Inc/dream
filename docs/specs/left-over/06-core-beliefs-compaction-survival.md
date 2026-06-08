# 06 — Core-beliefs digest survives compaction (left over from #13F)

**Deferred from:** `#13` slice 13F (governance standing-order injection). The
standing orders are injected into the system prompt every session; surviving
compaction is the deferred half.
**Status:** not built — blocked on a broader dormant seam (compaction carryover
metadata is not threaded in the live flow).

## What was deferred

Spec-04 contract: *"the core-beliefs digest survives [compaction]"*. The
machinery exists — `compact.create_core_beliefs_attachment_if_needed(metadata)`
reads `metadata["core_beliefs_digest"]` and re-attaches it after a compaction.
13F renders the standing-orders block and injects it into the system prompt, but
does **not** populate `core_beliefs_digest`, so a long session that compacts
could lose the constitution from context until the next session start.

## Why it was deferred

`run_session` calls `auto_compact_if_needed(...)` **without** `carryover_metadata`
(`engine/_session.py`). So *none* of the compaction attachments (exec-plan,
blocked-steps, orientation-brief, core-beliefs, house-rules) are threaded in the
live flow — the entire carryover-metadata path is dormant, not just the
core-beliefs digest. Wiring core-beliefs alone would be an inconsistent
half-measure; the right fix threads a `carryover_metadata` dict through
`SessionConfig -> QueryEngine.make_session_config -> build_query_engine ->
run_session -> auto_compact_if_needed`, populated by `build_default_harness`.

## Scope (when built)

- Add `carryover_metadata` to `SessionConfig` (and thread it through
  `QueryEngine`/`build_query_engine`), pass it to `auto_compact_if_needed`.
- `build_default_harness` sets `metadata["core_beliefs_digest"]` to the rendered
  standing-orders block (the same value injected into the system prompt).
- Ideally do this once for *all* the compaction attachments, not just
  core-beliefs (exec-plan, house-rules, etc.) — it's one threading change.

## Acceptance criteria

1. **MUST** preserve the core-beliefs digest across an in-session compaction
   (spec-04 contract), via `create_core_beliefs_attachment_if_needed`.
2. **SHOULD** thread the full compaction `carryover_metadata` (not only
   core-beliefs) so the other dormant attachments light up too.

## Dependencies

- `#13F` `render_standing_orders` — shipped; provides the digest value.
- `#04` compaction attachment builders — shipped; the consumer is ready, only
  the metadata threading remains.
