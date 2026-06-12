# 11a — Task Memory (dream's slice of memory)

**One-liner:** dream owns exactly one memory tier — **task memory**: a single free-form scratchpad
(`working-memory.md`) that lives and dies with the worktree, capped at 50 KB with in-place compression,
plus one **outbound seam** (`memory_propose`) that lets a task nominate a durable fact for promotion
*without* deciding its fate. Everything above the task clock — scoring, the dream phase, the wiki,
skill GC, governance — is **not dream's job**; it belongs to `lattice` (employee/growth clock) and
`horizon` (company/strategy clock).

**Sources (source of truth):** `docs/specs/divo/11-memory-and-self-evolution.md` — the parent spec.
11a is the **subset of 11 that runs inside the dream SDK**. It carries forward, verbatim-in-substance,
only the parts of 11 whose clock is the *turn/session* (dream's clock): the three-tier *read* model
(11 §"Key decisions" #1, already shipped as `MemoryStore`/`FileMemoryStore`), the working-memory file
and its lifetime (11 §"Artefact shapes" → *Working-memory file*; 11 decision #1, #6), the **50 KB cap →
compression** rule (11 decision #9; 11 §"Working-memory pressure"), the `propose_*` → queue seam (11
decision #2; 11 §"Promotion proposal flow"), and the `working_memory.compressed` event (11 decision #9).
**Everything else in 11 is explicitly delegated** (see §"Delegated — not dream's job").
**Reference (grounding only, not authority):** `dream-harness.md` §"four clocks" / `future.md` — the
four-product split (dream = the runtime/brain, one *task*, clock = turn+session · chorus = the org, one
*sprint*, clock = days · horizon = strategy, clock = months · lattice = employee growth, clock = months).
The dependency law (`dream` ← `dream.contracts` ← `chorus`/`horizon`/`lattice`; siblings never import each
other) is *why* dream must not contain the dream phase: consolidation reads many sessions across many
tasks — that is a clock above dream's, so it lives in lattice and reads dream's output through the
contracts seam (`MemoryStore` to read, `MemoryWriter` to apply, the `_proposals/` queue to receive
candidates). `src/dream/memory/__init__.py` already states this boundary: *"Curation and self-evolution
(the spec 11 brain) deliberately live outside the SDK — a `MemoryWriter` in the business repo (Model A)."*

---

## Why this matters

Spec 11 describes a months-long mind. dream is not a months-long mind — it is the thing that completes
**one task** and then is torn down. Conflating the two is the layering mistake this spec exists to
prevent. The four-clock model gives a crisp test for *"is this dream's job?"*: **what is the clock of
the thing being remembered?**

| Tier | Clock | Owner | Substrate |
|---|---|---|---|
| **task memory** | turn → session (seconds–hours) | **dream** | `.dream/sidecars/{task-id}/working-memory.md` — dies with the worktree |
| employee memory | months (growth) | **lattice** | `~/.dream/memory/{project}-{sha}/…` — the durable store dream *reads* |
| team memory | days (sprint) | **chorus** | sprint contract, standing-orders, role manifests |
| company memory | months (strategy) | **horizon** | `core-beliefs.md`, `docs/product-specs/`, the OKR tree |

dream owns the **first row only**. A task's working memory is the agent's own cognition mid-task: what it
figured out, open questions, things worth remembering later. It must be writable at will (it is the
agent thinking out loud), bounded (an unbounded scratchpad poisons the context window), and **mortal**
(it carries no authority beyond the task — promotion is somebody else's decision, made on a slower clock,
with evidence from *many* tasks).

Two failure modes follow from getting the boundary wrong:

- **dream tries to be its own curator.** If dream scored and promoted its own insights, a single task's
  enthusiasm would harden into doctrine — exactly the "superstitious system" 11 warns about. The guard is
  structural: dream can only *propose*; it physically cannot promote (no write path to the durable store
  except the append-only `_proposals/` queue).
- **The scratchpad grows without bound.** A long task accretes notes until working memory crowds out the
  actual context budget. The guard is the 50 KB cap with in-place compression and a garbage-rollback so a
  bad compression never destroys the agent's notes.

The deliberate non-goal: dream does **not** decide what is worth remembering across tasks. It emits
candidates and trusts the slower clock (lattice) to weigh them against everything else it has seen.

## Scope

**In (dream owns):**
- The **task-memory tier**: one `working-memory.md` per task, read/write/append, living under the
  worktree's sidecar so it dies with the worktree (decision #1, #2).
- The **50 KB cap → in-place compression** mechanism, with the original appended to
  `working-memory.history.md` and a garbage-output rollback guard (decision #3).
- The **`memory_propose(slug, content, rationale)`** tool — the outbound seam that writes a candidate to
  the durable `_proposals/` queue and returns. dream never reads its own proposals back (decision #4).
- Wiring all of the above behind an explicit **`build_harness(working_memory=True)`** flag so the surface
  is opt-in and the default tool set is unchanged (decision #5).
- The already-shipped **read** substrate (`MemoryStore`/`FileMemoryStore`, `memory_search`/`memory_get`,
  the system-prompt catalogue) — listed here only to mark that dream *reads* employee memory but does not
  *write* it.

**Out (delegated — see §"Delegated"):**
- The dream phase, scoring, the two-occurrence rule, skill GC, the wiki, the attic, the Dream Diary,
  `promote-explain` → **lattice**.
- `[governance-touch]` tagging, the push refusal, the governance PR gate → **horizon** (enforced via
  `#13` the permission/commit layer; the *policy* is horizon's).
- Session JSONL / progress.md (history, `#01`); exec-plans (active state, `#07`); evaluator logs (`#12`).
- Embedding / RAG retrieval — out for everyone in v1 (markdown + the existing scan/search).

## Delegated — not dream's job (and why)

Each item below is in spec 11 but its clock is above the task, so it is **explicitly out** of dream:

| Spec 11 feature | Clock | Lands in |
|---|---|---|
| dream phase (nightly `dream-curate` cron) | months (growth) | lattice |
| two-pass plan-then-act consolidation | months | lattice |
| backup → Change Manifest → keep/rollback verdict | months | lattice |
| consolidation lock | months | lattice |
| scored promotion gate (freq·rel·qdiv·recency) | months | lattice |
| two-occurrence rule | months | lattice |
| skill promotion + skill GC | months | lattice |
| wiki entries, slug-uniqueness, `_attic/`, `wiki.referenced` counting | months | lattice |
| Dream Diary + `promote-explain` | months | lattice |
| `[governance-touch]` tag + push refusal + governance PR gate | months (strategy) | horizon (via `#13`) |

The seam between dream and lattice is the contracts package (`dream.contracts.memory`): lattice reads the
`_proposals/` queue dream writes, scores candidates, and applies `MemoryDelta`s through a `MemoryWriter`
into the store dream reads via `MemoryStore`. dream depends on neither lattice nor the writer — only on
the queue location and the read protocol.

## Key decisions (assumed defaults)

1. **One tier, one file.** dream's writable memory is exactly `working-memory.md` under
   `paths.sidecar(task_id)` (`.dream/sidecars/{task-id}/`). It lives and dies with the worktree (`#02`):
   no teardown hook is needed because the worktree teardown already removes the sidecar tree.
2. **The agent writes its task memory freely.** `working_memory_read` / `working_memory_write` /
   `working_memory_append` are **safe, tier-0** tools (like `memory_get` / `skill`): they never touch the
   repo working tree, the network, or the durable store, so the sandbox tier never gates them. The agent
   can always journal, even under a read-only repo tier — task memory is cognition, not a repo effect.
3. **50 KB cap, in-place compression, garbage rollback.** When `working-memory.md` exceeds 50 KB the
   runtime may compress it: the original is appended to `working-memory.history.md`, the compressed text
   replaces the file atomically, and a `working_memory.compressed` outcome is produced (before/after
   bytes + status). A guard rolls back — **leaving the original untouched** — when the compressor returns
   empty or larger-than-original output. The compressor is **injected** (`Compressor = Callable[[str],
   Awaitable[str]]`) so dream hard-codes no provider; the mechanism is pure and unit-testable. The
   write/append tools additionally surface an over-cap **warning** in their result so the agent can
   self-compress (read → summarise → write back) even when no runtime compressor is wired.
4. **`memory_propose` is outbound-only.** `memory_propose(slug, content, rationale)` validates the slug,
   writes `{ts}-{slug}.md` to the durable proposals queue, and returns. The queue lives at
   `project_memory_dir(home, repo)/_proposals/` — in the dream **home**, not under the worktree — so a
   proposal **survives** the worktree teardown that kills working memory. dream does not read, score, or
   resolve proposals; that is lattice's dream phase. Proposal frontmatter carries `slug` / `source`
   (a `session://{id}` pointer) / `created` / `rationale`; the body is the candidate content.
5. **Opt-in behind a flag.** `build_harness(working_memory=False)` by default. When `True`, the four
   tools are registered and a per-session `TaskMemoryContext` (the `WorkingMemory` instance + the
   proposals dir + the source ref) is wired onto the dispatcher metadata — mirroring how `memory=` wires
   the read store. Default-off keeps the existing default tool schema byte-identical (prompt-cache
   stability, `#02`) and makes the feature explicitly opt-in.
6. **dream proposes; it never promotes.** There is no code path from a task to the durable store except
   the append-only `_proposals/` queue. This is the structural guarantee that one task's insight cannot
   become doctrine without the slower clock weighing in.

## Artefact shapes

### Working-memory file (`.dream/sidecars/{task-id}/working-memory.md`)

Free-form Markdown; suggested (not enforced) sections: *What I figured out* · *Open questions* ·
*Things to remember for later* (promotion candidates). Lives and dies with the worktree (`#02`).
Compressed in place at 50 KB; the pre-compression copy is appended to `working-memory.history.md`
alongside it (also mortal).

### Working-memory history (`.dream/sidecars/{task-id}/working-memory.history.md`)

Append-only debug aid. Each compression appends a block:

```markdown
<!-- compressed 2025-01-01T00:00:00+00:00 (51234 bytes) -->
<the full pre-compression working-memory.md>
```

### Compression outcome (in-memory, the `working_memory.compressed` event payload)

```python
CompressionOutcome(
    status="compressed" | "rolled_back" | "skipped",
    before_bytes=int,
    after_bytes=int,
    reason=str,
)
```

The runtime logs this as the `working_memory.compressed` event when it drives compression; the unit-level
contract is the returned value.

### Promotion proposal (`~/.dream/memory/{project}-{sha}/_proposals/{ts}-{slug}.md`)

```yaml
---
slug: retry-policy            # validated: lowercase a-z0-9 and hyphens, no path separators
source: session://s_abc123    # which session/task proposed it
created: 2025-01-01T00:00:00+00:00
rationale: "seen the same backoff bug fixed three ways; worth a durable note"
---

<proposed durable entry content>
```

Resolved (scored / merged / rejected) by lattice's dream phase — **never** by dream.

## Behaviour

### During a task (when `working_memory=True`)

- The agent reads employee memory freely (`memory_search` / `memory_get`, already shipped).
- The agent reads/writes/appends its task memory freely via the three `working_memory_*` tools.
- `working_memory_write` / `working_memory_append` succeed regardless of sandbox tier; when the resulting
  file exceeds 50 KB they return a non-error result whose metadata carries `warning: true` and a note to
  compress.
- The agent may `memory_propose(slug, content, rationale)` to nominate a durable fact; a `_proposals/`
  file is written to the home queue and the tool returns its path. The proposal is **not** acted on
  within the task.

### Working-memory pressure

The runtime may call `working_memory.maybe_compress(compressor)` (e.g. per turn). If the file is at or
under 50 KB it is a no-op (`status="skipped"`). Over 50 KB: the original is appended to
`working-memory.history.md`, the compressed output replaces the file atomically, and the outcome reports
`status="compressed"` with before/after bytes. If the compressor returns empty or larger-than-original
output, the file is left untouched and the outcome reports `status="rolled_back"`.

### Teardown

No explicit delete: working memory and its history live under the worktree's sidecar, which `#02` removes
when the worktree is torn down. Proposals are unaffected — they were written to the durable home queue.

## Acceptance criteria

1. **MUST** expose exactly one writable memory tier inside the SDK — task memory — backed by
   `working-memory.md` under the task sidecar.
2. **MUST** let the agent read, write, and append task memory without sandbox-tier gating (safe, tier 0).
3. **MUST** enforce a 50 KB working-memory cap by offering an in-place compression mechanism that appends
   the original to `working-memory.history.md` and replaces the file atomically.
4. **MUST** roll back compression (leaving the original file unchanged) when the compressor returns empty
   or larger-than-original output.
5. **MUST** produce a `working_memory.compressed` outcome (status + before/after bytes) when compression
   runs, skips, or rolls back.
6. **MUST** delete working memory and its history when the worktree is torn down (`#02`) — satisfied by
   placing both under the sidecar; no separate delete path.
7. **MUST** provide `memory_propose(slug, content, rationale)` that writes a `{ts}-{slug}.md` proposal to
   the durable home queue and returns, **without** reading, scoring, or resolving proposals.
8. **MUST** validate proposal slugs (lowercase `a-z0-9` + hyphens, no path separators / traversal) and
   surface the Spec 05 three-part error contract on a bad slug.
9. **MUST** write proposals to a location that survives worktree teardown (the dream home, not the
   worktree).
10. **MUST** keep all four tools behind `build_harness(working_memory=True)`; with the flag off the
    default tool registry and system prompt are unchanged.
11. **MUST** degrade gracefully — a "task memory not available in this session" message, not a crash —
    when a tool runs without a `TaskMemoryContext` wired.
12. **MUST NOT** implement the dream phase, scoring, the two-occurrence rule, skill GC, the wiki, the
    attic, the Dream Diary, or governance enforcement — those are delegated (§"Delegated").

## Acceptance scenarios

```gherkin
Feature: Task memory is the agent's mortal scratchpad

  Scenario: Agent journals to working memory under a read-only repo tier
    Given a session with working_memory enabled and a read-only sandbox tier
    When the agent calls working_memory_write with some notes
    Then the write succeeds
    And working_memory_read returns those notes

  Scenario: Append accumulates notes
    Given working memory already contains a line
    When the agent calls working_memory_append with a second line
    Then working_memory_read returns both lines in order

Feature: 50 KB cap and compression

  Scenario: Over-cap working memory is compressed in place
    Given working-memory.md is 51 KB
    When the runtime calls maybe_compress with a compressor that shrinks it
    Then working-memory.md is replaced with the compressed text
    And the original is appended to working-memory.history.md
    And the outcome status is "compressed" with before_bytes > after_bytes

  Scenario: Under-cap working memory is left alone
    Given working-memory.md is 1 KB
    When the runtime calls maybe_compress
    Then the file is unchanged
    And the outcome status is "skipped"

  Scenario: Garbage compression rolls back
    Given working-memory.md is 51 KB
    When the runtime calls maybe_compress with a compressor that returns empty or larger output
    Then working-memory.md is left untouched
    And the outcome status is "rolled_back"
    And no history block is appended

  Scenario: Write past the cap warns the agent
    Given working memory enabled
    When the agent writes content larger than 50 KB
    Then the write succeeds
    And the result metadata carries warning: true

Feature: Outbound proposal seam

  Scenario: Agent proposes a durable memory entry
    Given a session with working_memory enabled
    When the agent calls memory_propose("retry-policy", "...", "...")
    Then a file {ts}-retry-policy.md is created in the durable proposals queue
    And the file survives worktree teardown
    And dream does not read or resolve it

  Scenario: Bad slug is rejected with the three-part contract
    Given a session with working_memory enabled
    When the agent calls memory_propose("../escape", "...", "...")
    Then the tool returns an error
    And the error carries root_cause / safe_retry / stop_condition

Feature: Opt-in surface

  Scenario: Flag off leaves the default surface unchanged
    Given build_harness is called with the default working_memory=False
    Then working_memory_read / working_memory_write / working_memory_append / memory_propose
      are absent from the tool registry

  Scenario: Flag on wires the tools and the context
    Given build_harness is called with working_memory=True
    Then the four task-memory tools are registered
    And a TaskMemoryContext is wired onto the session
```

## Tests

- `test_working_memory_read_write_roundtrip`
- `test_working_memory_append_accumulates`
- `test_working_memory_read_empty_when_absent`
- `test_working_memory_size_and_over_cap`
- `test_working_memory_size_triggers_compression` — over-cap `maybe_compress` compresses.
- `test_compression_replaces_file_in_place`
- `test_compression_history_appended`
- `test_compression_under_cap_skips` — no-op + `status="skipped"`.
- `test_compression_garbage_larger_rolls_back`
- `test_compression_garbage_empty_rolls_back`
- `test_compression_outcome_reports_bytes_and_status` — the `working_memory.compressed` payload.
- `test_proposal_tool_creates_proposal_file`
- `test_proposal_written_to_durable_home_not_worktree`
- `test_proposal_slug_validated` — bad slug → three-part error.
- `test_working_memory_tools_are_safe_tier_0`
- `test_memory_propose_is_safe_tier_0`
- `test_task_memory_tools_degrade_without_context`
- `test_build_harness_working_memory_flag_registers_tools`
- `test_build_harness_default_omits_task_memory_tools`
- `test_build_harness_working_memory_wires_context`

## Edge cases

- **Compressor returns larger-or-equal output.** Treated as garbage → rollback, original untouched, no
  history block. (Byte comparison is on UTF-8 length.)
- **Compressor returns whitespace-only output.** Treated as empty → rollback.
- **`maybe_compress` called when the file does not exist.** `size == 0 ≤ cap` → `status="skipped"`, no IO.
- **Two proposals with the same slug in the same second.** Filenames collide (`{ts}-{slug}`); the later
  overwrites. lattice's dream phase dedups by slug regardless, so this is benign; the second-resolution
  rule lives there, not here.
- **`memory_propose` with an empty slug or one with uppercase / spaces / slashes.** Rejected by the slug
  validator with the three-part error contract; no file written.
- **Working memory written under a read-only repo tier.** Still succeeds — task memory is harness-internal
  cognition (decision #2), not a sandboxed repo write.
- **Tool invoked with `working_memory=True` registration but `memory=False`.** Independent: the proposals
  dir is derived from `project_memory_dir` directly, not from the read store, so it works regardless.

## Open questions

- Cap unit: bytes (default, cheap) vs tokens (more accurate against the context budget, costlier).
- Whether `maybe_compress` should be driven automatically per turn by the engine loop (a `#03` change) or
  remain runtime-driven via an explicit call — v1 ships the mechanism and the over-cap warning; automatic
  per-turn triggering is a follow-up that needs the turn-loop seam.
- Whether the proposal queue should be the dream **home** (`_proposals/`, chosen here for durability) or a
  committed in-repo path once dream runs against a non-worktree checkout — lattice's reader must agree.
- Whether `memory_propose` should serialise a `MemoryDelta` (contracts) instead of markdown-with-
  frontmatter — markdown is human-greppable and matches 11's proposal shape; revisit if lattice prefers
  the typed delta on the wire.

## Out of scope

- The dream phase and every months-clock primitive in spec 11 (§"Delegated") — lattice.
- Governance tagging / push refusal / PR gate — horizon (enforced at `#13`).
- Embedding / RAG retrieval over employee memory — v1 is scan + search for everyone.
- Cross-task or cross-repo working memory — task memory is per-worktree by definition.
- Writing to the durable employee store from inside dream — forbidden; the only outbound path is the
  append-only `_proposals/` queue.
