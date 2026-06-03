# 10 — Memory & Self-Evolution

**One-liner:** Three memory tiers (working / static / wiki) with explicit promotion gates. A nightly dream phase curates the wiki. Governance prevents the agent from rewriting its own constitution.

**Sources:** [ANT-1], [LC] · taxonomy §15, §18

---

## Why this matters

Long-running agents accumulate two kinds of knowledge: **what happened** (events, logs, prior turns) and **what we learned** (patterns, conventions, decisions worth re-using). Conflating them is the source of most agent-memory dysfunction:

- Stuffing every observation into one bucket guarantees the bucket is too noisy to query and too large to summarise.
- Letting the agent freely rewrite its own beliefs guarantees belief drift — six weeks in, the agent's "core principles" bear little resemblance to what the team agreed.
- Forgetting to garbage-collect guarantees the wiki becomes a graveyard of contradictory advice.

The fix is three tiers with explicit boundaries, a dedicated curation phase (the *dream phase*) that runs off the hot path, and hard governance gates around the small set of files the agent must never rewrite without human review.

## Scope

**In:** the three memory tiers and their lifetimes, promotion rules, the dream-phase ritual, wiki garbage-collection, governance auto-merge blocks, the agent's read/write surface for each tier.

**Out:** the session JSONL and progress.md (these are *history*, owned by #01); exec-plans (these are *active state*, owned by #09); evaluator tuning logs (→ #07); RAG / embedding infrastructure (deferred — wiki is markdown + grep in v1).

## Assumed defaults

- **Three tiers, three files-or-folders:**
  - **Working memory** — `.harness/sidecars/{task-id}/working-memory.md`. Per-task, ephemeral, deleted with the worktree (#02). Agent writes freely.
  - **Static memory** — `docs/design-docs/`, `docs/product-specs/`, `docs/SECURITY.md`, `docs/design-docs/core-beliefs.md`. Human-curated; agent reads freely, writes only via PR (auto-merge-blocked).
  - **Wiki memory** — `docs/wiki/`. Agent-curated long-term knowledge. Read freely, written by the dream phase, GC'd quarterly.
- **Promotion is explicit.** Nothing slides between tiers automatically except via the dream phase. The agent can *propose* promotions during a session, but they're queued and applied later.
- **Dream phase = a nightly cron** (kind: `dream-curate`, see #09). Runs as a normal session, reads the day's sessions + working memories + tech-debt entries + evaluation records, produces wiki edits and promotion proposals.
- **Wiki entries are atomic.** One file per entry under `docs/wiki/{slug}.md`. Frontmatter declares `created`, `last_used`, `times_referenced`, `confidence`. No nested folders in v1.
- **Wiki GC quarterly.** Entries with `times_referenced == 0` and `created` >90 days ago move to `docs/wiki/_attic/`. Entries with `confidence: low` and `last_used` >180 days ago move to `_attic/`. Nothing is deleted; attic files persist until human review.
- **Governance auto-merge blocks** for: `docs/design-docs/core-beliefs.md`, anything under `docs/product-specs/`. PRs touching these require a `[governance]` label and a human approval comment matching a configured pattern (e.g. `LGTM-governance`).
- **Working memory has a per-task cap** of 50 KB. Past the cap, the runner forces a working-memory summarisation (small LLM call producing a compressed replacement) before continuing.
- **Agents reference wiki entries by slug**, not by full path. Slugs are unique within `docs/wiki/`; the validator (#01) enforces uniqueness.
- **Wiki write access is dream-phase-only.** Generator/evaluator subagents can read but not write `docs/wiki/`. Attempts to write outside the dream phase are refused at the tool layer (#05).
- **Promotion proposals queue at `docs/wiki/_proposals/`.** Each proposal is a markdown file; dream phase reviews the queue and either merges into a wiki entry or rejects with a note.

## Artefacts

### Working-memory file (`.harness/sidecars/{task-id}/working-memory.md`)

Free-form. Suggested structure (not enforced):
- "What I figured out this session" — bullet list of insights.
- "Open questions" — bullets the agent didn't resolve.
- "Things to remember for later" — promotion candidates.

Lives and dies with the worktree.

### Static memory

Files already covered by other specs:
- `docs/design-docs/core-beliefs.md` — the constitution (#08).
- `docs/design-docs/conventions.md` — coding conventions.
- `docs/product-specs/*.md` — what we're building.
- `docs/SECURITY.md` — secret rules.

These are *consumed* by the agent, written by humans.

### Wiki entry (`docs/wiki/{slug}.md`)

Frontmatter (YAML):
```yaml
slug: <unique>
created: <ISO date>
last_used: <ISO date>
times_referenced: <int>
confidence: high | medium | low
sources: ["session://...", "exec-plan://...", "ref://..."]
```

Body: free Markdown. One concept per file.

### Promotion proposal (`docs/wiki/_proposals/{ts}-{slug}.md`)

- Source — pointer back to the working memory or session that proposed it.
- Proposed entry — the candidate wiki content.
- Rationale — why this should be promoted.

### Attic (`docs/wiki/_attic/{slug}.md`)

Same shape as a wiki entry, plus a frontmatter field `attic_reason` and `attic_at`.

## Behaviour

### During a session

- The agent reads any tier freely.
- The agent writes to working memory at will.
- If the agent tries to write to `docs/wiki/`, the tool layer refuses with a clear error pointing at the proposal queue.
- If the agent tries to edit a static-memory file, the tool layer allows the write (we don't block edits — protection is at the PR boundary), but the runner emits a warning event and tags the resulting commit with `[governance-touch]`.

### Working-memory pressure

- Per-turn, the runner measures the working-memory file size.
- At 50 KB, the runner spawns a small LLM call: "compress this working memory file preserving every actionable insight; remove redundancy; output replaces the file."
- Compression result replaces the file; original is appended to `.harness/sidecars/{task-id}/working-memory.history.md` (debug aid, deleted with worktree).
- Event `working_memory.compressed` is logged.

### Promotion proposal flow

- During any session, the agent can call `propose_wiki_entry(slug, content, rationale)`.
- The runner writes the proposal under `docs/wiki/_proposals/` and commits it with the session-end commit (#01).
- The next dream-phase run picks up new proposals.

### Dream phase

1. Cron triggers `dream-curate` daily (default 03:00 local).
2. Runner starts a session with sandbox tier `repo-write` and a specialised entry prompt.
3. Session reads:
   - All sealed session JSONLs from the prior 24h.
   - Any new files under `docs/wiki/_proposals/`.
   - The current wiki state.
   - The current tech-debt tracker.
4. Session produces:
   - New wiki entries (one file per concept).
   - Updates to existing wiki entries (incrementing `times_referenced`, refreshing `last_used`, updating `confidence`).
   - Resolutions of `_proposals/` items (merge into entry or reject with note).
5. Session does *not* touch static memory; it can append to a "dream-phase suggestions" file under `docs/exec-plans/active/` if it thinks human attention is needed.
6. Session ends, JSONL sealed, commit produced.

### Wiki GC

Quarterly (1st of Jan/Apr/Jul/Oct), the `dream-curate` run includes a GC pass:
- Move entries to `_attic/` per the policy above.
- Emit a GC summary in the session JSONL: counts moved, slugs moved, total wiki size.

### Wiki reference counting

When any session loads a wiki entry's content into context (via a `read_wiki(slug)` tool), the runner emits a `wiki.referenced` event. The next dream-phase run aggregates these and updates `times_referenced` + `last_used` on the relevant entries.

### Static-memory edits

- The agent edits a static file normally (write tool succeeds).
- The runner detects the edit at session end, tags the commit with `[governance-touch]`, and refuses to push to the default branch.
- The change must go through a PR. CI / branch protection (operator config) enforces:
  - PR title or label includes `[governance]`.
  - A human approver comments with the configured magic string (default: `LGTM-governance`).
- Cron jobs (#09) MUST NOT attempt to merge governance-tagged PRs.

## Acceptance criteria

1. **MUST** treat the three tiers as separate stores with separate write rules.
2. **MUST** refuse direct writes to `docs/wiki/` from any session other than the dream-phase session.
3. **MUST** route wiki additions/edits through the `_proposals/` queue or the dream-phase session itself.
4. **MUST** allow the agent to read any tier without restriction.
5. **MUST** enforce a working-memory size cap (default 50 KB) by triggering a compression call.
6. **MUST** delete the working memory file when the worktree is torn down (#02).
7. **MUST** preserve uniqueness of wiki slugs (validator-enforced).
8. **MUST** maintain wiki frontmatter (`times_referenced`, `last_used`, `confidence`) via dream-phase updates.
9. **MUST** include a quarterly GC pass in the dream phase that moves stale entries to `_attic/` without deleting.
10. **MUST** tag commits touching static memory with `[governance-touch]`.
11. **MUST** refuse to push commits tagged `[governance-touch]` directly to the default branch.
12. **MUST NOT** allow cron jobs (#09) to auto-merge governance-tagged PRs.
13. **SHOULD** log a `wiki.referenced` event on every wiki read so the dream phase can update counters.
14. **SHOULD** keep the `_attic/` indefinitely; deletion requires explicit human action.
15. **SHOULD** include a "dream-phase suggestions" output mechanism for items needing human attention without rewriting static memory.

## Gherkin

```gherkin
Feature: Tier write boundaries

  Scenario: Generator session refused write to wiki
    Given a generator session is running
    When the agent calls write_file on docs/wiki/foo.md
    Then the write is refused
    And the error points the agent at propose_wiki_entry

  Scenario: Agent proposes a wiki entry
    Given a session in progress
    When the agent calls propose_wiki_entry("retry-policy", "...", "...")
    Then a file docs/wiki/_proposals/{ts}-retry-policy.md is created
    And the proposal is included in the session-end commit

  Scenario: Dream phase picks up proposals
    Given two proposals exist under docs/wiki/_proposals/
    When the nightly dream-curate cron runs
    Then both proposals are resolved (merged or rejected)
    And the _proposals/ directory is empty afterwards

  Scenario: Working memory compression triggered
    Given a task's working-memory.md is 51 KB
    When the next turn starts
    Then a compression LLM call is made
    And working-memory.md is replaced with the compressed version
    And working_memory.compressed event is logged

  Scenario: Wiki GC moves stale entry to attic
    Given a wiki entry created 95 days ago with times_referenced == 0
    When quarterly GC runs
    Then the entry moves to docs/wiki/_attic/
    And the entry's frontmatter gains attic_at and attic_reason

  Scenario: Static memory edit blocks push to default branch
    Given the agent edits docs/design-docs/core-beliefs.md
    When session-end runs
    Then the commit is tagged [governance-touch]
    And the runner refuses to push it to the default branch
    And the runner instructs the operator to open a PR

  Scenario: Cron refuses to auto-merge governance PR
    Given a doc-garden cron run produced a [governance-touch] commit
    When the cron tries to auto-merge
    Then the merge is refused
    And the cron run summary records the refusal
```

## Tests

- `test_generator_session_cannot_write_wiki` — write_file refused for `docs/wiki/*`.
- `test_proposal_tool_creates_proposal_file` — `propose_wiki_entry` puts a file in `_proposals/`.
- `test_proposal_included_in_session_commit` — proposal file part of the session-end commit.
- `test_dream_phase_consumes_all_proposals` — after dream phase, `_proposals/` is empty.
- `test_dream_phase_can_write_wiki` — dream-phase session has write access; verified by a test run.
- `test_working_memory_size_triggers_compression` — at 51 KB, compression fires.
- `test_compression_replaces_file_in_place` — file shrunk; content reduced.
- `test_compression_history_appended` — original preserved in `working-memory.history.md`.
- `test_working_memory_compressed_event_logged` — JSONL entry present.
- `test_working_memory_deleted_with_worktree` — sidecar teardown removes it.
- `test_wiki_slug_uniqueness_enforced` — validator blocks duplicate slugs.
- `test_wiki_reference_event_logged_on_read` — `read_wiki(slug)` produces an event.
- `test_dream_phase_updates_times_referenced` — counter increases after dream run.
- `test_quarterly_gc_moves_unused_entries_to_attic` — `times_referenced == 0` + age threshold → attic.
- `test_quarterly_gc_moves_low_confidence_stale` — `confidence: low` + `last_used` age → attic.
- `test_quarterly_gc_does_not_delete` — files only move; nothing removed.
- `test_attic_entries_retain_frontmatter` — atticised entries have `attic_at` + `attic_reason`.
- `test_static_memory_edit_tags_commit` — commit message contains `[governance-touch]`.
- `test_governance_tag_blocks_default_branch_push` — push refused, operator instructed.
- `test_cron_refuses_to_merge_governance_pr` — `doc-garden` cron does not auto-merge.
- `test_agent_reads_static_memory_freely` — no restriction on reads.
- `test_agent_reads_wiki_freely` — no restriction on reads.

## Edge cases

- **Dream phase fails mid-run.** Partial state is sealed normally. Next run picks up the remaining `_proposals/` and any wiki updates left undone (idempotent — re-applying the same delta is safe because dream-phase edits are diff-based, not append-based).
- **Two proposals with the same proposed slug.** Dream phase merges them into one entry, preserving rationale from both in the entry's body.
- **Operator edits a wiki entry directly.** Allowed (humans can edit anywhere). Next dream-phase run treats the edit as authoritative and folds it into its analysis.
- **Working memory compression returns garbage.** Runner detects (e.g. compressed version is larger than original, or empty) and rolls back; emits an error event; next turn proceeds with original.
- **Wiki entry references a session JSONL that has since been archived.** Dream phase follows the archive pointer; if the archive is unreachable, marks the source as `unverified` in the frontmatter.
- **Static memory file deleted entirely** (e.g. `core-beliefs.md` removed). Caught by the next session-start validator (#01) as a missing required file → blocks.
- **Concurrent dream-phase and operator wiki edits.** Operator wins; dream phase uses a 3-way merge against the wiki state it loaded.

## Open questions

- Should the working-memory cap be byte-based (current default) or token-based? Token-based is more accurate but more expensive to compute per turn.
- Should `times_referenced` decay (e.g. only the last 90 days count)? Current default: pure lifetime counter; revisit if entries with old high counts dominate.
- Do we want per-session "quick wiki" — entries promoted within the same session by an explicit operator command? Current default: no, everything goes through the proposal queue.
- Should the dream phase be allowed to *delete* attic entries after another 180 days? Current default: no, only humans delete.

## Out of scope

- Embedding-based retrieval / RAG for the wiki. v1 is markdown + filename slugs + grep. RAG can be layered later without changing this spec.
- Cross-repo memory (a wiki shared between projects). Each repo has its own wiki by design.
- Versioning of wiki entries beyond what git provides.
- A graphical wiki browser (deferred; markdown is the medium).
- Automatic promotion from working memory directly to static memory — explicitly forbidden; humans only.
