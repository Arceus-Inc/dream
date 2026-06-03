# 01 — Repo as System of Record

**One-liner:** The repo is the only authoritative source the agent reads from or writes to. `AGENTS.md` is the table of contents; everything else is filed under `docs/`. A session-start validator refuses to run if the contract is broken.

**Sources:** [OAI], [ANT-1], [LC] · taxonomy §11, §16, §14, §21

---

## Why this matters

Across all five sources, the single biggest predictor of a harness working over weeks of autonomous operation is that the repo itself encodes the contract: what to read, what to write, where to look for prior decisions, and what counts as "done." When state lives outside the repo (in databases, in an operator's head, in chat history), the harness becomes uninspectable and unreproducible. Putting everything in the repo also means git is doing most of the durability work for free — diffs, rollbacks, branches, blame all come along for the ride.

The cost of this discipline is real: the agent has to keep the repo navigable, the validator has to be strict, and humans have to accept that `docs/` is *the* product, not a side artefact. This spec defines the minimum contract.

## Scope

**In:** `AGENTS.md` format, required `docs/` tree, session artefacts, the session-start validator, the initializer that bootstraps an empty repo, JSON schema discipline.

**Out:** the content of design docs themselves (governance, not format); exec-plan internals (#09); memory tiering (#10); the actual session FSM (#03).

## Key decisions (assumed defaults)

1. **One repo per session.** No cross-repo references, no symlinks out. Monorepo support is deferred — we can add per-package `AGENTS.md` later without breaking this contract.
2. **`AGENTS.md` at root only, single canonical filename.** No `CLAUDE.md`, `.cursorrules`, etc. Tooling that needs a different name can symlink.
3. **Soft cap 100 lines, hard fail at 300.** Past 100 the validator warns; past 300 it refuses to start. Forces the file to stay a table of contents, not become a manual.
4. **Required tree** (paths must exist with valid content):
   - `AGENTS.md`
   - `docs/design-docs/core-beliefs.md`
   - `docs/exec-plans/active/` (folder; may be empty)
   - `docs/product-specs/` (folder; may be empty)
   - `docs/references/` (folder; may be empty)
   - `docs/sessions/` (folder; may be empty)
   - `docs/SECURITY.md`
   - `init.sh` (or `init.ps1` on Windows-only repos)
5. **Agent can write anywhere in the repo.** Protection comes from git review on the resulting commit, not from a write-block. The validator catches the structural issues at session start, not in-flight.
6. **JSON-with-schema for status, Markdown for narrative.** Every JSON file declares `$schema` pointing into `docs/_schemas/`. Markdown carries human-readable rationale.
7. **Two session artefacts:**
   - Rolling `docs/progress.md` — append-at-top, human-skimmable, auto-archived when it crosses 1000 lines.
   - Per-session `docs/sessions/{YYYY-MM-DD}-{session-id}.jsonl` — machine-readable event stream, sealed at session end (a final close event, then the file is committed).
8. **Session-start validator has three severities:**
   - **blocking** — session refuses to start; operator must fix.
   - **warning** — session starts; surfaced to the agent in its first turn.
   - **info** — recorded, not surfaced.
9. **Initializer is an LLM-driven agent with a `--no-ai` deterministic fallback.** Operators in restricted environments can scaffold without calling an external model.
10. **Validator engine is hybrid:** built-in core checks + per-repo plug-ins declared in `.harness/validators/`.

## Artefact shapes (described, not coded)

### `AGENTS.md`

- One-line "what this repo is."
- A pointer to `docs/design-docs/core-beliefs.md`.
- A short table of contents — section headings + links — covering: design docs, product specs, references, current exec-plan, security notes.
- Optional: a "house rules" section (e.g. "never delete migration files"). House rules carried here are duplicated into `core-beliefs.md` for canonicalisation.

### `docs/progress.md`

- Newest entry at the top.
- Each entry: ISO timestamp, session id, one-line summary, link to the sealed jsonl.
- Old entries roll off automatically (see auto-archive below).

### Per-session jsonl

- One JSON object per line.
- Required fields: `ts`, `session_id`, `event_type`, `payload`.
- Event types include but are not limited to: `session.start`, `orientation.complete`, `turn.start`, `tool.call`, `tool.result`, `validator.finding`, `session.end`.
- File is closed by a `session.end` event; once closed it is read-only and committed.

### Required JSON schemas (in `docs/_schemas/`)

- `progress-entry.schema.json`
- `session-event.schema.json`
- `validator-finding.schema.json`
- `exec-plan-ledger.schema.json` (defined in #09 but enforced here)

## Behaviours

### Session start (validator pass)

1. Operator invokes the harness in a repo.
2. Validator runs the five blocking checks and any per-repo plug-ins.
3. If any blocking finding: print findings, exit non-zero, refuse to start.
4. If only warnings/infos: continue to orientation (see #03). Warnings are written to the first turn's context.

### Session end (seal)

1. Agent or runner emits `session.end`.
2. Runner writes the close event to the jsonl, makes the file read-only, stages it.
3. Runner prepends a new entry to `progress.md`.
4. Runner commits both files in one commit; commit message references session id.

### Auto-archive

When `progress.md` exceeds 1000 lines at session-end, the runner moves the bottom half (oldest entries) to `docs/progress.archive/{YYYY-Qn}.md` and commits the move alongside the new entry. Archive files are append-only.

### Initializer (fresh repo)

1. Operator runs `harness init` in an empty or near-empty repo.
2. Initializer agent reads whatever exists, asks the operator a small number of questions (project name, primary language, primary substrate), and scaffolds the required tree.
3. Initializer commits the scaffold with a `[harness:init]` tag.
4. Subsequent sessions pass the validator.
5. With `--no-ai`, the initializer scaffolds a generic template and prompts the operator to fill in `core-beliefs.md`.

## Acceptance criteria

### Blocking checks (MUST)

1. **MUST** refuse to start a session if `AGENTS.md` is missing.
2. **MUST** refuse to start if `AGENTS.md` exceeds the hard cap (300 lines by default).
3. **MUST** refuse to start if any link in `AGENTS.md` does not resolve to an existing file or section.
4. **MUST** refuse to start if any required path in the tree is missing.
5. **MUST** refuse to start if any JSON file under `docs/` fails its declared `$schema`.
6. **MUST** refuse to start if a secret-shaped string is detected in any file under `docs/` (basic patterns: AWS keys, common API token prefixes, PEM blocks, JWTs).

### Soft checks (SHOULD)

7. **SHOULD** warn (not block) when `AGENTS.md` exceeds the soft cap (100 lines).
8. **SHOULD** warn on exec-plans untouched for more than 7 days.
9. **SHOULD** warn on links that resolve but point to empty files.
10. **SHOULD** surface all warnings to the agent in its first turn.

### Session artefacts (MUST)

11. **MUST** load `AGENTS.md`, `docs/progress.md`, and the validator findings into the agent's first turn.
12. **MUST** end every session with a commit that contains the new `progress.md` entry and the sealed session jsonl.
13. **MUST** seal the jsonl with a `session.end` event before commit.
14. **MUST** never modify a sealed jsonl on subsequent sessions.

### Auto-archive (MUST/SHOULD)

15. **MUST** archive `progress.md` deterministically when it exceeds the configured threshold at session end.
16. **SHOULD** name archive files using a calendar quarter (e.g. `2026-Q2.md`).

### Initializer (MUST)

17. **MUST** ship an initializer that, given an empty repo, produces a passing tree in one invocation.
18. **MUST** offer a `--no-ai` mode that does not contact any external model.
19. **MUST** commit the scaffold with a tagged commit message.

### Validator extensibility (SHOULD)

20. **SHOULD** auto-discover per-repo validators from `.harness/validators/`.
21. **SHOULD** let per-repo validators downgrade (but not upgrade) the severity of built-in findings, with an audit trail in the validator finding event.

## Acceptance scenarios

```gherkin
Scenario: Missing AGENTS.md blocks session
  Given a repo with no AGENTS.md at the root
  When the operator starts a session
  Then the runner prints a blocking finding for "AGENTS.md missing"
  And the runner exits non-zero
  And no session jsonl is created.

Scenario: Oversized AGENTS.md blocks session
  Given an AGENTS.md with 401 lines
  And a hard cap of 300
  When the operator starts a session
  Then the runner prints a blocking finding for "AGENTS.md exceeds hard cap"
  And exits non-zero.

Scenario: Secret in docs blocks session
  Given a file under docs/ containing a string matching the AWS access key pattern
  When the operator starts a session
  Then the runner prints a blocking finding identifying the file
  And exits non-zero
  And the finding does not echo the secret value itself.

Scenario: Stale exec-plan warns but does not block
  Given an exec-plan under docs/exec-plans/active/ untouched for 10 days
  When the operator starts a session
  Then the runner emits a warning finding
  And the session proceeds to orientation
  And the warning appears in the agent's first turn.

Scenario: progress.md crosses threshold and auto-archives
  Given progress.md is 1005 lines at session end
  When the runner seals the session
  Then the runner moves the oldest entries to docs/progress.archive/{quarter}.md
  And progress.md drops below the threshold
  And both files are part of the session-end commit.

Scenario: Initializer scaffolds a fresh repo
  Given an empty git repo
  When the operator runs `harness init --no-ai`
  Then the required tree exists with placeholder content
  And a tagged commit "[harness:init]" is on HEAD
  And a subsequent session starts without blocking findings.

Scenario: Per-repo validator downgrades a built-in finding
  Given a per-repo validator that downgrades "stale exec-plan" from warning to info
  When the validator runs
  Then the finding is recorded with severity "info"
  And the finding event includes a reference to the downgrading validator.
```

## Tests

- `test_missing_agents_md_blocks_session` — happy-path negative for the most common breakage.
- `test_oversized_agents_md_blocks_session` — enforces the hard cap.
- `test_undersized_agents_md_warns` — enforces the soft cap as a warning, not a block.
- `test_dead_link_in_agents_md_blocks` — links must resolve.
- `test_link_to_empty_file_warns` — soft signal for incomplete docs.
- `test_required_path_missing_blocks` — tree contract.
- `test_invalid_json_status_blocks` — schema discipline.
- `test_secret_in_docs_blocks` — covers the standard secret patterns.
- `test_secret_finding_does_not_echo_secret` — finding must redact.
- `test_stale_exec_plan_warns_not_blocks` — soft path.
- `test_warnings_surface_in_first_turn` — agent sees them.
- `test_first_turn_includes_agents_md_and_progress` — context loading.
- `test_session_end_seals_jsonl` — close event present.
- `test_session_end_produces_commit` — single commit, both files.
- `test_sealed_jsonl_is_read_only_after_commit` — no rewriting history.
- `test_progress_md_auto_archives_at_threshold` — archive triggers.
- `test_archive_filename_uses_calendar_quarter` — naming convention.
- `test_initializer_scaffolds_fresh_repo_to_passing_state` — bootstrap works.
- `test_initializer_no_ai_mode_does_not_call_model` — restricted-env compliance.
- `test_initializer_commits_with_tagged_message` — auditability.
- `test_per_repo_validator_discovered` — extensibility.
- `test_per_repo_validator_can_downgrade_but_not_upgrade_built_in_finding` — safety rail.
- `test_validator_severity_downgrade_recorded_in_finding` — audit trail.

## Edge cases

- **Symlinked `AGENTS.md`.** Allowed; validator follows the link and treats the target as authoritative.
- **Binary files under `docs/`.** Validator skips them but records an info finding so curators can review.
- **Git submodules under `docs/`.** Not supported in v1; validator emits a warning recommending vendoring.
- **A required folder exists but is git-ignored.** Treated as missing (the repo is the source of truth, not the working tree).
- **`init.sh` vs `init.ps1`.** Either satisfies the requirement; presence of both is allowed.
- **Concurrent sessions in the same repo.** Out of scope for #01; see #02 (per-worktree isolation).

## Open questions

- Whether `AGENTS.md` line counts should exclude code fences and tables.
- Whether the hard cap should be byte-based instead of line-based (to handle CJK-heavy repos).
- Whether secret-detection patterns should be operator-configurable or fixed (current default: fixed list, with override via `docs/_security/secret-patterns.toml`).

## Out of scope

- Memory tiers (→ #10).
- Exec-plan state machine and ledger semantics (→ #09).
- Per-package `AGENTS.md` for monorepos (deferred).
- Per-worktree isolation, locking, and concurrency (→ #02).
- The actual session FSM and orientation prompt (→ #03).
