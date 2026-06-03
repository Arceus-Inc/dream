# 11 — Memory & Self-Evolution

**One-liner:** A months-long mind separates **what happened** from **what we learned**, and never lets
the agent rewrite its own constitution. Three memory tiers (working / static / wiki) with
explicit promotion gates, a nightly **dream phase** that consolidates in two passes (decide-then-apply)
behind a backup→manifest→verdict safety net, a **scored promotion gate** with a **two-occurrence rule**
so one-shot weirdness never hardens into doctrine, **skill garbage-collection** (the missing half every
source admits), and hard governance blocks around the small set of files only a human may change.

**Sources (source of truth):** `docs/specs/new-specs/10-memory-and-self-evolution.md` — the **three
tiers** (working `.harness/sidecars/{task-id}/working-memory.md` ephemeral / static `docs/design-docs/`
+ `docs/product-specs/` + `docs/SECURITY.md` + `core-beliefs.md` human-curated / wiki `docs/wiki/`
agent-curated), **explicit promotion** (nothing slides between tiers except via the dream phase; agents
*propose*, the dream phase *applies*), the **dream phase as a nightly cron** (`dream-curate`, `#07`),
**atomic wiki entries** (`docs/wiki/{slug}.md` with `created`/`last_used`/`times_referenced`/
`confidence`/`sources` frontmatter, one concept per file, unique slugs), **quarterly wiki GC** to
`docs/wiki/_attic/` (never delete: `times_referenced==0` + age>90d, or `confidence:low` +
`last_used`>180d), **governance auto-merge blocks** (`core-beliefs.md` + `docs/product-specs/` require a
`[governance]` label + a human `LGTM-governance` approval; cron MUST NOT auto-merge governance PRs), the
**50 KB working-memory cap** triggering a compression call, **wiki-write-is-dream-phase-only** (other
sessions refused at the tool layer, routed to `propose_wiki_entry` → `docs/wiki/_proposals/`),
`wiki.referenced` event counting, and the full acceptance criteria / Gherkin / `test_*` set are carried
forward verbatim-in-substance and enriched here. That spec's lineage ([ANT-1] memory tiers, [LC]
long-term memory, [AHE] self-evolution, taxonomy §15/§18) is the conceptual authority. The enrichment
draws the **slow-clock primitives** (`plans/dream-harness-synthesis.md` §"month clock") the conceptual
spec gestures at but leaves unwritten: **two-phase plan-then-act consolidation** with a success-only
cursor, the **scored promotion gate** (frequency · relevance · query-diversity · recency), the
**two-occurrence rule** (a workflow becomes a skill only after it appears *twice*), **memory-as-markdown-
in-git** (git log = belief history, git blame = staleness prior, git revert = rollback a bad
consolidation, PR = how a belief change is approved), the human-facing **Dream Diary** + **promote-
explain**, and **skill GC** (prune-if-never-retrieved + merge-near-duplicates — the half every source
admits is missing). · `#07` (the `dream-curate` cron that triggers the dream phase; the tech-debt
tracker it reads) · `#06` (the skills the two-occurrence rule promotes *into* and skill GC prunes
*from*; the precedence ladder a promoted skill must respect) · `#02` (the worktree working memory lives
and dies with) · `#01` (session JSONL = the dream phase's raw input; the slug-uniqueness validator;
session-end commit) · `#05` (the tool-layer refusal that blocks non-dream wiki writes) · `#13` (the
governance gate that blocks `[governance-touch]` pushes; the PermissionChecker behind it) · `#12` (the
evaluation records the dream phase reads as consolidation signal).
**Reference (grounding only, not authority):** [openharness] —
`services/autodream/service.py` (`try_acquire_consolidation_lock` / `read_last_consolidated_at` /
`list_sessions_touched_since` / `rollback_consolidation_lock`, `_has_dream_signal` (only consolidate
when recent sessions are worth it), `build_consolidation_prompt`, session-end listener + periodic
`SESSION_SCAN_INTERVAL_SECONDS` scan, runs **scoped in a child process** `OPENHARNESS_AUTODREAM_CHILD`;
prompt rule "create at most 2 new markdown files in one dream"), `services/autodream/backup.py`
(`create_memory_backup` (timestamped copytree before consolidating), `diff_memory_dirs` →
`{added, removed, changed}` file-name lists — **the concrete Change Manifest shape**) — the
backup→diff→rollback triad is the grounding for this spec's manifest+verdict safety net,
`memory/usage.py` (`load_usage_index` / `mark_memory_used` / `find_stale_memory_candidates`,
`get_usage_index_path` — the per-entry usage counter that drives GC; the grounding for
`times_referenced`/`last_used`), `services/memory_extract/__init__.py` (`ExtractionRecord`
(title/body/memory_type/scope/description/tags) / `ExtractionResult` (skipped/reason/records),
`build_memory_manifest`, `add_memory_entry`, `MEMORY_WRITE_TOOLS={write_file,edit_file}` — durable
extraction from completed turns; the grounding for the propose→queue flow), `memory/schema.py`
(`MemoryType` = `user|feedback|project|reference`, `MemoryScope` = `private|project|team`, stable
frontmatter field order; the grounding for typed/scoped entries), `memory/types.py` (`MemoryHeader`
frontmatter: `confidence`-adjacent `importance`, `ttl_days`, `supersedes`, `source`, `created_at`/
`updated_at`, `disabled`, `tags` — the richer frontmatter vocabulary). Used to name the consolidation-
lock, dream-signal, change-manifest, usage-index, and extraction-record primitives concretely.
**Two divergences are deliberate and called out below:** OpenHarness stores memory + backups under a
*global* home dir (`~/.ohmo`, `get_data_dir()`), but the conceptual spec's repo-only rule wins — our
wiki, proposals, attic, Dream Diary, and per-dream backups all live *inside the repo* under `docs/wiki/`
so `git` *is* the belief history; and OpenHarness's flat `user|feedback|project|reference` taxonomy is
*not* our tier model — we keep the three-tier working/static/wiki split as the authority, borrowing the
frontmatter vocabulary (confidence, ttl, supersedes, source) but not the type taxonomy.

---

## Why this matters

A long-running agent accumulates two kinds of knowledge, and conflating them is the source of most
agent-memory dysfunction. **What happened** (events, logs, prior turns) is *history* — owned by `#01`
(session JSONL) and `#07` (exec-plans). **What we learned** (patterns, conventions, decisions worth
re-using) is *memory* — and it is this spec's job. Three failure modes follow from getting the boundary
wrong:

- **One noisy bucket.** Stuff every observation into a single store and it is too noisy to query and too
  large to summarise. → Three tiers with separate write rules and lifetimes.
- **Belief drift.** Let the agent freely rewrite its own beliefs and, six weeks in, its "core
  principles" bear no resemblance to what the team agreed. → Static memory is human-curated, edited only
  through a governance-gated PR; the agent can read it but never push a change to it.
- **A graveyard of contradictory advice.** Never garbage-collect and the wiki fills with stale,
  conflicting entries nobody trusts. → A dedicated curation phase off the hot path, plus quarterly GC
  that *moves* (never deletes) stale entries to an attic.

The conceptual spec (new-spec 10) gets the three-tier skeleton and the dream-phase ritual right. The
synthesis (`dream-harness-synthesis.md`) showed where it stops short — the **slow-clock primitives**
that turn "a folder of markdown" into a mind that improves without drifting:

1. **Consolidation is two-phase everywhere.** Pass 1 *decides what should change* with **no file
   tools** — it can only read and emit directives. Pass 2 *applies* those directives with write tools
   **scoped to one directory**. A cursor advances **only on success**, so a crash never loses or
   duplicates a batch. This is the single most-repeated pattern across every memory system surveyed.
2. **Promotion is scored, not eager.** A fact earns its way into permanent memory by a weighted gate —
   **frequency · relevance · query-diversity · recency**. The query-diversity weight is the subtle one:
   it stops one chatty thread from spamming permanent memory with facts nobody else ever recalls.
3. **A workflow becomes a skill only after it appears twice.** The whole danger of self-evolution is a
   one-shot fluke hardening into permanent doctrine. "Twice" is the cheapest possible guard, and it is
   the rule that separates a learning system from a superstitious one.
4. **Memory is markdown-in-git, on purpose.** `git log` is belief history; `git blame` answers "when did
   it start believing this" (and feeds *staleness* into the consolidator as a prior, so the model sees
   which facts are old without being forced to delete them); `git revert` rolls back a bad
   consolidation; a PR is how a human approves a belief change. This is *why* everything lives in the
   repo, not a home dir.
5. **Skills get garbage-collected.** Every source admits the same gap: skills only ever get *added* —
   none prune. The missing half is **prune-if-never-retrieved** and **merge-near-duplicates**, with
   git-derived per-line age as the staleness template.

OpenHarness shipped the *mechanism* for the dream phase — a consolidation lock, a "is there even a
signal worth consolidating" check, a backup-then-diff safety net, a per-entry usage index that drives
GC, and a child-process scope so a dream never pollutes the live session. We borrow those shapes and add
the manifest+verdict discipline on top: every dream is reversible, every dream explains itself.

## Scope

**In:**
- The three tiers, their lifetimes, read/write surfaces, and the rule that promotion is explicit.
- The dream phase: trigger, the two-pass plan-then-act structure, the success-only cursor, the
  backup→**Change Manifest**→**verdict** (keep / rollback) safety net, the child-process scope.
- The scored promotion gate (frequency · relevance · query-diversity · recency) and the
  **two-occurrence rule** for skill promotion.
- Working-memory pressure handling (50 KB cap → compression).
- Wiki entry shape, slug uniqueness, reference counting, the proposal queue.
- Quarterly wiki GC and **skill GC** (prune-if-never-retrieved, merge-near-duplicates).
- Governance: static-memory edit detection, `[governance-touch]` tagging, the push block, the
  no-cron-auto-merge rule.
- The human-facing **Dream Diary** + **promote-explain** output.

**Out:**
- Session JSONL and progress.md — these are *history*, owned by `#01`.
- Exec-plans — *active state*, owned by `#07`.
- The skills *runtime* (loading, progressive disclosure, the precedence ladder) — owned by `#06`; this
  spec only owns *promoting into* and *GC-ing out of* the skill set.
- Evaluator tuning logs (→ `#12`).
- The cron scheduler itself (→ `#07`); this spec owns the `dream-curate` *session*, not the trigger.
- RAG / embedding retrieval for the wiki — deferred (wiki is markdown + grep in v1; the **QA cache** and
  **commitments** primitives are named as seams below but not shipped).
- Cross-repo / shared memory — each repo has its own wiki by design.

## Key decisions (assumed defaults)

1. **Three tiers, three write rules.** Working memory: agent writes freely, dies with the worktree
   (`#02`). Static memory: agent reads freely, writes *only* via a governance-gated PR. Wiki: agent
   reads freely, writes *only* via the dream phase (other sessions refused at the tool layer, `#05`).
2. **Promotion is explicit and dream-phase-only.** Nothing moves between tiers automatically. A session
   may `propose_wiki_entry(slug, content, rationale)`; the proposal lands in `docs/wiki/_proposals/`
   and is committed with the session-end commit (`#01`). The next dream run decides its fate.
3. **The dream phase is two-pass, plan-then-act.** **Pass 1 (plan)** runs with a *read-only* toolset:
   it reads the prior 24h of sealed session JSONLs, new `_proposals/`, current wiki state, the
   tech-debt tracker (`#07`), and recent evaluation records (`#12`), and emits a **directive list**
   (what to create / update / reject) — it may *not* touch files. **Pass 2 (act)** runs with write
   tools **scoped to `docs/wiki/` only** and applies the directives surgically. A **cursor** (the
   `last_consolidated_at` marker) advances **only after Pass 2 succeeds**, so a crash re-runs the same
   batch idempotently rather than losing or duplicating it.
4. **Every dream is reversible and self-explaining.** Before Pass 2, the runner takes a **backup** of
   `docs/wiki/`. After Pass 2, it computes a **Change Manifest** = `{added, removed, changed}` slug
   lists (`diff_memory_dirs`-shaped). The dream then renders a **verdict**: *keep* (commit the change +
   the manifest into the session-end commit) or *rollback* (restore the backup, advance no cursor, emit
   an error) — rollback fires automatically when the manifest is empty-but-expected, or when a sanity
   check regresses (e.g. wiki shrank to zero, a required entry vanished). Git is the durable rollback
   substrate (`git revert` a kept-then-regretted dream).
5. **Promotion is scored, not eager.** A `_proposals/` item or a recurring working-memory insight is
   admitted to the wiki only if its weighted score clears a threshold:
   `score = w_freq·frequency + w_rel·relevance + w_qdiv·query_diversity + w_rec·recency`. The
   **query-diversity** term down-weights facts seen only inside one thread, so a single chatty task
   cannot spam permanent memory. The score and its reasons are recorded (decision #8).
6. **The two-occurrence rule gates skill promotion.** A workflow observed in the dream input is promoted
   to a *skill* (`#06`) only after it has appeared in **≥ 2 distinct sessions**. First occurrence is
   *noted* (a `_proposals/`-style skill candidate with `occurrences: 1`); the second occurrence flips it
   eligible. This is the guard against one-shot weirdness becoming doctrine.
7. **Skills are garbage-collected.** A quarterly **skill GC** pass (part of the `dream-curate` quarterly
   run) moves skills to an attic when **never retrieved** (no `skill.referenced` event since creation,
   age > threshold) and **merges near-duplicates** (two skills whose bodies are near-identical fold into
   one, preserving both names as aliases per `#06`'s precedence ladder). GC never deletes; it
   attics. The precedence ladder (`#06`) is respected: GC may never attic or override a higher-trust
   (bundled/core-safety) skill with a lower-trust one.
8. **Wiki entries are scored and counted.** Frontmatter carries `created` / `last_used` /
   `times_referenced` / `confidence` / `sources`. Every `read_wiki(slug)` emits a `wiki.referenced`
   event; the dream phase aggregates these (via the usage index) to refresh `times_referenced` +
   `last_used` and re-derive `confidence`.
9. **Working memory has a 50 KB cap.** Past it, the runner makes a small compression LLM call
   ("compress preserving every actionable insight; remove redundancy; output replaces the file"); the
   original is appended to `working-memory.history.md` (debug aid, dies with the worktree); a
   `working_memory.compressed` event is logged. A guard rolls back if compression returns garbage
   (larger-than-original or empty).
10. **Governance protects the constitution.** Edits to `core-beliefs.md` or anything under
    `docs/product-specs/` are *allowed* at the tool layer (we don't block edits) but the session-end
    detector tags the commit `[governance-touch]`, **refuses to push it to the default branch**, and
    requires a PR with a `[governance]` label + a human `LGTM-governance` approval. **Cron jobs (`#07`)
    MUST NOT auto-merge governance-tagged PRs.**
11. **The dream runs scoped, off the hot path.** The dream session runs as its own `dream-curate` cron
    session (`#07`) in its own context — ideally a child process — so its large reads and edits never
    pollute a live working session. A **consolidation lock** prevents two concurrent dreams.
12. **The dream is human-legible.** Each run appends a **Dream Diary** entry (`docs/wiki/_diary/
    {date}.md`) summarising what changed and *why*, and supports a **promote-explain** query that, for
    any slug or rejected proposal, prints the score breakdown and the decision — so a human can always
    answer "why did (or didn't) the agent remember this?"

## Artefact shapes

### Working-memory file (`.harness/sidecars/{task-id}/working-memory.md`)

Free-form; suggested (not enforced) sections: *What I figured out* · *Open questions* · *Things to
remember for later* (promotion candidates). Lives and dies with the worktree (`#02`). Compressed in
place at 50 KB; pre-compression copy appended to `working-memory.history.md`.

### Static memory (human-curated, consumed by the agent)

`docs/design-docs/core-beliefs.md` (the constitution, `#13`), `docs/design-docs/conventions.md`,
`docs/product-specs/*.md`, `docs/SECURITY.md`. Written by humans through governance-gated PRs.

### Wiki entry (`docs/wiki/{slug}.md`)

```yaml
slug: <unique>            # validator-enforced uniqueness (#01)
created: <ISO date>
last_used: <ISO date>
times_referenced: <int>
confidence: high | medium | low
sources: ["session://...", "exec-plan://...", "ref://..."]
score: { frequency, relevance, query_diversity, recency, total }   # enrichment: why it was promoted
supersedes: [<slug>, …]   # optional; borrowed from MemoryHeader vocabulary
```
Body: free Markdown, one concept per file. No nested folders in v1.

### Promotion proposal (`docs/wiki/_proposals/{ts}-{slug}.md`)

`source` (pointer to the working memory / session that proposed it) · `proposed entry` (candidate
content) · `rationale`. Resolved by the next dream (merged into an entry or rejected with a note).

### Skill candidate (`docs/wiki/_skill-candidates/{slug}.md`) — enrichment

A workflow seen once. Frontmatter `occurrences: <int>` + `sessions: [<id>, …]`. Promoted to a real
skill (`#06`) only at `occurrences ≥ 2` across *distinct* sessions (decision #6).

### Change Manifest (`docs/wiki/_diary/{date}.md` body, or a sidecar) — enrichment

`{ added: [slug…], removed: [slug…], changed: [slug…], verdict: keep|rollback, reason }`. The
`diff_memory_dirs`-shaped record of what the dream did, committed with the run.

### Attic (`docs/wiki/_attic/{slug}.md`)

Same shape as a wiki entry plus `attic_reason` and `attic_at`. Retained indefinitely; only humans
delete. Skill GC writes skill attic entries the same way.

## Behaviour

### During a session

- Reads any tier freely.
- Writes working memory at will.
- A `write_file` to `docs/wiki/` is **refused** at the tool layer (`#05`) with an error pointing at
  `propose_wiki_entry`.
- A `write_file` to a static-memory file **succeeds** (we protect at the PR boundary, not the tool
  layer), but the runner emits a warning event and the session-end commit is tagged
  `[governance-touch]`.

### Working-memory pressure

Per turn the runner measures the file size. At 50 KB it spawns the compression call, replaces the file,
appends the original to `working-memory.history.md`, and logs `working_memory.compressed`. A guard
detects garbage output (larger/empty) and rolls back to the original, emitting an error event.

### Promotion proposal flow

`propose_wiki_entry(slug, content, rationale)` writes a `_proposals/` file committed at session end. The
next dream picks it up, scores it (decision #5), and either folds it into a wiki entry or rejects it with
a note recorded for `promote-explain`.

### Dream phase (the `dream-curate` session)

1. Cron (`#07`) triggers `dream-curate` daily (default 03:00 local). It runs scoped (child process,
   own context), having acquired the **consolidation lock** (refuse if another dream holds it).
2. **Pass 1 (plan, read-only):** read the prior 24h sealed JSONLs (`list_sessions_touched_since` the
   cursor), new `_proposals/`, new `_skill-candidates/`, current wiki, tech-debt tracker (`#07`),
   recent evaluation records (`#12`). If there is **no dream signal** (no touched sessions, no
   proposals), exit cleanly without a backup. Otherwise emit a directive list (create / update / reject
   / promote-skill / atticise).
3. **Backup:** copy `docs/wiki/` to a timestamped in-repo backup before any write.
4. **Pass 2 (act, writes scoped to `docs/wiki/`):** apply directives surgically — create/update entries
   (refresh `times_referenced`/`last_used`/`confidence` from the usage index), resolve every
   `_proposals/` item, promote eligible skill candidates into `#06` skills (two-occurrence rule),
   atticise per GC policy.
5. **Manifest + verdict:** compute the Change Manifest (`diff_memory_dirs`); render *keep* (commit) or
   *rollback* (restore backup, advance no cursor, error event). On *keep*, **advance the cursor**
   (`last_consolidated_at`) — only now.
6. Append a **Dream Diary** entry. The dream **does not** touch static memory; if it thinks a human
   should change the constitution it appends a note to a "dream-phase suggestions" file under
   `docs/exec-plans/active/`.
7. Session ends, JSONL sealed, commit produced.

### Wiki GC (quarterly, inside the dream)

On the 1st of Jan/Apr/Jul/Oct the dream includes a GC pass: move entries to `_attic/` per policy
(`times_referenced==0` + created>90d; or `confidence:low` + `last_used`>180d), and run **skill GC**
(decision #7: prune-never-retrieved, merge-near-duplicates, respect the precedence ladder). Emit a GC
summary (counts, slugs, total size) in the JSONL and Dream Diary. Nothing is deleted.

### Static-memory edits & governance

The write succeeds; the session-end detector tags the commit `[governance-touch]`, **refuses to push to
the default branch**, and instructs the operator to open a PR. Branch protection (operator config)
requires a `[governance]` label + a human `LGTM-governance` comment. Cron jobs (`#07`) refuse to
auto-merge any governance-tagged PR and record the refusal in their run summary.

## Acceptance criteria

1. **MUST** treat the three tiers as separate stores with separate write rules.
2. **MUST** refuse direct writes to `docs/wiki/` from any session other than the dream-phase session.
3. **MUST** route wiki additions/edits through `_proposals/` or the dream-phase session itself.
4. **MUST** allow the agent to read any tier without restriction.
5. **MUST** enforce a working-memory size cap (default 50 KB) by triggering a compression call.
6. **MUST** delete the working-memory file when the worktree is torn down (`#02`).
7. **MUST** preserve uniqueness of wiki slugs (validator-enforced, `#01`).
8. **MUST** maintain wiki frontmatter (`times_referenced`, `last_used`, `confidence`) via dream-phase
   updates driven by the usage index.
9. **MUST** include a quarterly GC pass that moves stale wiki entries to `_attic/` without deleting.
10. **MUST** tag commits touching static memory with `[governance-touch]`.
11. **MUST** refuse to push `[governance-touch]` commits directly to the default branch.
12. **MUST NOT** allow cron jobs (`#07`) to auto-merge governance-tagged PRs.
13. **MUST** run consolidation as two passes — a read-only plan pass that emits directives with no file
    tools, then a write pass scoped to `docs/wiki/` — and advance the cursor only on Pass-2 success.
14. **MUST** take a backup before Pass 2 and render a keep/rollback verdict from a Change Manifest;
    rollback MUST restore the backup and advance no cursor.
15. **MUST** hold a consolidation lock so two dreams never run concurrently.
16. **MUST** promote a workflow to a skill (`#06`) only after it appears in ≥ 2 distinct sessions.
17. **MUST** score promotions on frequency · relevance · query-diversity · recency and record the score
    + reasons on the entry.
18. **MUST** respect the skill precedence ladder (`#06`) — GC/merge MUST NOT attic or override a
    higher-trust skill with a lower-trust one.
19. **SHOULD** log a `wiki.referenced` event on every wiki read.
20. **SHOULD** keep `_attic/` indefinitely; deletion requires explicit human action.
21. **SHOULD** include a "dream-phase suggestions" mechanism for items needing human attention without
    rewriting static memory.
22. **SHOULD** append a Dream Diary entry per run and support a `promote-explain` query that prints any
    slug/proposal's score breakdown and decision.
23. **SHOULD** run skill GC quarterly (prune-never-retrieved + merge-near-duplicates), atticising not
    deleting.

## Acceptance scenarios

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

Feature: Two-phase consolidation with manifest and verdict

  Scenario: Plan pass cannot touch files
    Given the dream phase is in Pass 1 (plan)
    When the plan pass attempts a write_file
    Then the write is refused
    And the plan pass may only emit directives

  Scenario: Cursor advances only on success
    Given a dream is consolidating a batch of sessions
    When Pass 2 crashes before completion
    Then the consolidation cursor is not advanced
    And the next dream re-runs the same batch idempotently

  Scenario: Bad consolidation rolls back from backup
    Given Pass 2 produced a manifest whose sanity check regresses (wiki emptied)
    When the verdict is rendered
    Then the backup is restored
    And the cursor is not advanced
    And an error event is emitted
    And no wiki entry was lost

  Scenario: Kept dream commits its Change Manifest and advances the cursor
    Given Pass 2 added two entries and changed one
    When the verdict is "keep"
    Then the manifest {added:2, changed:1} is committed with the run
    And last_consolidated_at advances
    And a Dream Diary entry is appended

  Scenario: Concurrent dreams blocked by the consolidation lock
    Given one dream holds the consolidation lock
    When a second dream-curate session starts
    Then it refuses to consolidate and exits cleanly

Feature: Scored, gated promotion

  Scenario: One chatty thread cannot spam permanent memory
    Given a fact appears 20 times but only inside a single session thread
    When the dream scores it
    Then the query-diversity term lowers its total below threshold
    And the fact is not promoted

  Scenario: Two-occurrence rule gates skill promotion
    Given a workflow appeared in exactly one session
    When the dream evaluates skill candidates
    Then it is recorded with occurrences=1 and not promoted
    And after a second distinct session it becomes eligible and is promoted to a skill

  Scenario: promote-explain prints the decision
    Given a proposal was rejected last night
    When a human runs promote-explain on its slug
    Then the score breakdown and rejection reason are printed

Feature: GC and pressure

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

  Scenario: Skill GC prunes a never-retrieved skill
    Given a skill created last quarter with no skill.referenced events
    When quarterly skill GC runs
    Then the skill is moved to the skill attic
    And a higher-trust bundled skill with the same name is left untouched

Feature: Governance

  Scenario: Static memory edit blocks push to default branch
    Given the agent edits docs/design-docs/core-beliefs.md
    When session-end runs
    Then the commit is tagged [governance-touch]
    And the runner refuses to push it to the default branch
    And the runner instructs the operator to open a PR

  Scenario: Cron refuses to auto-merge governance PR
    Given a dream-curate cron run produced a [governance-touch] commit
    When the cron tries to auto-merge
    Then the merge is refused
    And the cron run summary records the refusal
```

## Tests

- `test_generator_session_cannot_write_wiki`
- `test_proposal_tool_creates_proposal_file`
- `test_proposal_included_in_session_commit`
- `test_dream_phase_consumes_all_proposals`
- `test_dream_phase_can_write_wiki`
- `test_plan_pass_has_no_file_tools` — Pass 1 write refused.
- `test_act_pass_scoped_to_wiki_dir` — Pass 2 cannot write outside `docs/wiki/`.
- `test_cursor_advances_only_on_success` — crash mid-Pass-2 leaves cursor unmoved.
- `test_dream_idempotent_rerun_of_same_batch` — re-running a batch is safe.
- `test_dream_takes_backup_before_act_pass`
- `test_change_manifest_lists_added_removed_changed`
- `test_bad_consolidation_rolls_back_from_backup`
- `test_kept_dream_commits_manifest_and_advances_cursor`
- `test_consolidation_lock_blocks_concurrent_dreams`
- `test_no_dream_signal_exits_without_backup`
- `test_promotion_score_includes_query_diversity` — chatty-thread fact not promoted.
- `test_two_occurrence_rule_gates_skill_promotion`
- `test_skill_candidate_records_occurrences`
- `test_promote_explain_prints_score_and_decision`
- `test_dream_diary_entry_appended_per_run`
- `test_working_memory_size_triggers_compression`
- `test_compression_replaces_file_in_place`
- `test_compression_history_appended`
- `test_compression_garbage_rolls_back` — larger/empty output reverts.
- `test_working_memory_compressed_event_logged`
- `test_working_memory_deleted_with_worktree`
- `test_wiki_slug_uniqueness_enforced`
- `test_wiki_reference_event_logged_on_read`
- `test_dream_phase_updates_times_referenced`
- `test_quarterly_gc_moves_unused_entries_to_attic`
- `test_quarterly_gc_moves_low_confidence_stale`
- `test_quarterly_gc_does_not_delete`
- `test_attic_entries_retain_frontmatter`
- `test_skill_gc_prunes_never_retrieved`
- `test_skill_gc_merges_near_duplicates`
- `test_skill_gc_respects_precedence_ladder` — higher-trust skill untouched.
- `test_static_memory_edit_tags_commit`
- `test_governance_tag_blocks_default_branch_push`
- `test_cron_refuses_to_merge_governance_pr`
- `test_agent_reads_static_memory_freely`
- `test_agent_reads_wiki_freely`

## Edge cases

- **Dream phase fails mid-run.** Cursor unmoved (decision #3); partial wiki state is the backup-restored
  state (verdict rollback) or, if Pass 2 committed before the verdict failed, the next run re-derives
  the delta diff-based and is idempotent. Either way nothing is lost or double-applied.
- **Two proposals with the same proposed slug.** Dream merges them into one entry, preserving both
  rationales in the body.
- **Operator edits a wiki entry directly.** Allowed (humans edit anywhere). The next dream treats the
  edit as authoritative and 3-way-merges against the wiki state it loaded; operator wins on conflict.
- **Working-memory compression returns garbage.** Detected (larger-than-original or empty) → rollback to
  original + error event; next turn proceeds with the original.
- **Wiki entry references an archived session JSONL.** Dream follows the archive pointer; if
  unreachable, marks the source `unverified` in frontmatter rather than dropping the entry.
- **Static memory file deleted entirely** (e.g. `core-beliefs.md` removed). Caught by the next
  session-start validator (`#01`) as a missing required file → blocks.
- **A skill candidate's two occurrences are in the *same* session re-run twice.** Not eligible — the
  rule requires two *distinct* sessions; a re-run of one session counts once.
- **Near-duplicate skills differ only in a higher-trust vs lower-trust scope.** GC merges *toward* the
  higher-trust skill and aliases the lower-trust name; it never folds the higher into the lower.
- **Backup directory itself grows unbounded.** Backups are in-repo and pruned by their own age policy
  (kept N most recent); git history is the long-tail rollback substrate.

## Open questions

- Working-memory cap byte-based (default) vs token-based (more accurate, costlier per turn).
- Whether `times_referenced` should decay (only last-90-days count) vs pure lifetime counter.
- The exact weights `w_freq / w_rel / w_qdiv / w_rec` and the promotion threshold — start conservative,
  tune from the Dream Diary.
- Whether the dream may *delete* (not just attic) entries after a further 180 days — default no, humans
  only.
- Whether to ship the **QA cache** (voyager: self-built FAQ with vector dedup, a long-haul cost saver)
  and **commitments** (openclaw: inferred, TTL'd, per-day-capped follow-ups) now or defer — both are
  named here as seams; v1 defers (no vector infra).
- Whether skill GC's "near-duplicate" test should be textual (cheap, v1) or embedding-based (accurate,
  needs the deferred vector infra).

## Out of scope

- Embedding-based retrieval / RAG for the wiki — v1 is markdown + slugs + grep; layers on later without
  changing this spec.
- The QA cache and commitments primitives as *shipped* features — named as seams, deferred.
- The skills runtime, progressive disclosure, and precedence-ladder *enforcement* (→ `#06`); this spec
  only promotes into and GCs out of the skill set.
- Cross-repo / shared memory — each repo owns its wiki.
- Versioning of wiki entries beyond what git provides.
- A graphical wiki browser (markdown is the medium).
- Automatic promotion from working memory directly to static memory — explicitly forbidden; humans
  only.
