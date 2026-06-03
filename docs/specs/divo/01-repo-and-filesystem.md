# 01 — Repo as System of Record & Filesystem/Worktree Isolation

**One-liner:** Durable state is committed files, ephemeral state is per-task worktrees and
sidecars, and the boundary between them is the single most load-bearing decision in the
harness — get it right and git buys you diffs, blame, revert, branches, and crash recovery
for free; get it wrong and the harness becomes uninspectable and unreproducible.

**Sources (source of truth):** `docs/specs/new-specs/01-repo-as-system-of-record.md` +
`02-filesystem-and-worktree.md` — the two conceptual specs this consolidates and enriches;
their decisions, criteria, scenarios, and edge cases are carried forward in full · `#00`
storage layout · `self_improving.md` Part IV (the repo *is* the dataset).
**Reference (grounding only, not authority):** [openharness] `swarm/worktree.py`
(`WorktreeManager`, `WorktreeInfo`, `validate_worktree_slug`, `_flatten_slug`,
`_symlink_common_dirs`), `utils/fs.py` (`atomic_write_bytes`, `atomic_write_text`),
`utils/file_lock.py` (`exclusive_file_lock`), `config/paths.py` — used to name primitives
concretely where the conceptual specs left them abstract.

---

## Why this matters

Cross-cutting invariant #2 (`#00`) states the bet plainly: **the repo is the system of
record.** Every other component in the harness is downstream of *where state lives*. If
durable state lives in a process's memory, the first crash erases the work. If it lives in an
external database, the harness is no longer reproducible from a clone and no longer
inspectable with `git log`. If it lives in an operator's head, autonomous operation is
impossible by definition. The discipline this spec enforces is that **the only authoritative
state is committed files**, and the only sanctioned exception is the coordination store
(`#08`) which holds *now*-state, not *of-record* state.

The second half of the spec is the dual of the first: if committed files are durable, then
*everything else must be disposable*. Many tasks run concurrently against one repo — cron
jobs (`#07`), autopilot cards (`#09`), swarm workers (`#10`), dream-phase curators (`#11`).
If they share a working tree, a SQLite file, or a log directory, they corrupt each other in
non-obvious ways. The convergent answer across every mature harness is **per-task isolation
at the filesystem layer**: a fresh git worktree per task, all sidecar state colocated under
one directory, torn down as a unit. OpenHarness implements exactly this in
`swarm/worktree.py`; we adopt its layout and naming.

The cost is real and worth naming: the agent must keep the repo navigable, writes must route
through an atomic-write helper, and cross-platform file semantics (POSIX `fcntl` vs Windows
`msvcrt`/`LockFileEx`) must be hidden behind a thin layer so no other component ever sees
them. This spec pays that cost once, here, so specs 02–13 can assume it.

## Scope

**In:** the two storage roots and their split (of-record vs now); the `AGENTS.md` contract
and session-start validator; the required `docs/` tree; worktree lifecycle
(create/resume/remove/cleanup) modelled on `WorktreeManager`; slug validation and flattening;
common-dir symlinking; the sidecar bundle; the atomic-write contract
(`atomic_write_bytes`/`atomic_write_text`); cross-platform exclusive file locking
(`exclusive_file_lock`); checkpoint refs and resume-from-checkpoint; teardown.

**Out:** what runs *inside* a worktree — the turn loop (`#03`), tools (`#05`); the
coordination CAS store and claim protocol that decides *who* owns a worktree (`#08`); network
sandboxing and path-permission enforcement (`#13`); cron scheduling and the task ledger
(`#07`); the autopilot promotion policy that decides how a finished worktree reaches mainline
(`#09`); memory tiering (`#11`); multi-host distribution (single-host v1 throughout).

## Key decisions

1. **Two roots, hard split between *of-record* and *now*.** Per-project committed state lives
   in the repo (`docs/`, `.harness/checkpoints` refs); per-user global config and cross-project
   state live under `~/.harness/` (mirroring OpenHarness's `~/.openharness/`, resolved by the
   `config/paths.py` accessors). The *now*-state stores (`.harness/coordination/`,
   per-task sidecars) are git-ignored and never committed.

2. **One repo per session; `AGENTS.md` at root is the table of contents.** A single canonical
   filename (no `CLAUDE.md`/`.cursorrules` proliferation; tooling that needs another name
   symlinks). Soft cap 100 lines (warn), hard cap 300 lines (block). It points at
   `docs/`; it does not *become* `docs/`.

3. **One worktree per task; the task is the unit of isolation.** A "session" (`#03`) runs
   inside exactly one worktree. Two tasks may run in two worktrees concurrently against the
   same base repo; two tasks in the *same* worktree is forbidden.

4. **Worktrees live under a flat, git-ignored directory keyed by slug.** Following
   `WorktreeManager`, the path is `<base>/<flat-slug>/` where `_flatten_slug` replaces `/`
   with `+` to keep the layout flat and avoid nested branch/dir collisions. In-repo layout is
   `.harness/worktrees/{task-id}/`. Each worktree checks out a generated branch
   `worktree-{flat-slug}` (created with `git worktree add -B`, so an orphan branch left by a
   prior remove is reset rather than colliding).

5. **Slugs are validated, never trusted.** `validate_worktree_slug` is a security boundary,
   not a nicety: max 64 chars, each `/`-segment must match `[a-zA-Z0-9._-]+`, `.`/`..`
   segments rejected (path traversal), absolute paths rejected. A task-id that fails
   validation aborts task start with a blocking error — it never reaches the filesystem.

6. **Create is idempotent (fast-resume).** If the worktree directory already exists and
   `git rev-parse --git-dir` succeeds inside it, `create_worktree` returns the existing
   `WorktreeInfo` without re-running `git worktree add`. This makes crash-resume cheap: the
   recovering runner calls create with the same slug and gets the live worktree back.

7. **Large common dirs are symlinked, not copied.** `_symlink_common_dirs` links
   `node_modules`, `.venv`, `__pycache__`, `.tox` from the main repo into the worktree.
   Symlink failure (disk full, unsupported fs) is non-fatal — the worktree still works, just
   without the shared cache. Teardown removes these symlinks *before* `git worktree remove`
   so git never tries to delete the shared originals.

8. **Each worktree gets a colocated sidecar bundle** under `.harness/sidecars/{task-id}/`:
   `db.sqlite` (per-task structured state), `logs/`, `metrics/`, `scratch/` (tool-output
   offload — `#04`), and `state.json` (schema-validated task state). The bundle is deleted
   *with* the worktree on teardown.

9. **Every harness-initiated write is atomic (temp → fsync → rename).** Routed through
   `atomic_write_bytes`/`atomic_write_text`: write to `{path}.tmp.{uuid}` in the same
   directory, fsync the file, rename over the destination, fsync the directory on POSIX.
   A crash mid-write leaves the prior version fully intact. Readers never observe a torn
   file. This is what makes "the repo is the system of record" *safe* under crashes.

10. **Cross-platform locking is hidden behind one context manager.** `exclusive_file_lock`
    serialises read-modify-write on shared registries (`state.json`, cron, memory index,
    swarm mailbox), using `fcntl.flock` on POSIX and `msvcrt.locking` on Windows, releasing
    on context exit *and* on process exit (the OS drops the lock when the fd closes). It
    raises `SwarmLockUnavailableError` on unsupported platforms rather than silently
    degrading. Pair every critical section with an atomic write to be both race-free and
    crash-safe.

11. **Checkpoints are git refs, not branches.** After every successful turn end the runner
    commits the worktree and writes the SHA to `refs/harness/checkpoints/{task-id}/{n}`; the
    final success checkpoint is `.../done`. These refs are invisible to `git branch -a`, so
    they do not clutter the human-facing branch view, and they **survive worktree teardown** —
    which is the whole point: the disposable thing (worktree) dies, the durable thing
    (checkpoint) persists.

12. **Resume creates a *new* task from any checkpoint ref.** The scheduler/operator spawns a
    fresh worktree (new task-id) at a checkpoint ref; the new task records
    `state.json.parent_checkpoint_ref` for lineage and otherwise behaves identically to a
    fresh task. Task-ids are never reused.

13. **No worktree-to-worktree communication.** Tasks coordinate only through committed state
    on the base branch and the coordination store (`#08`) — never by reading each other's
    sidecars. This keeps failures attributable (invariant #3) and isolation real.

## Artefact shapes (described, not coded)

### Storage layout (the two roots)

```
# Per-project, committed (system of record)
<repo>/
  AGENTS.md                          # table of contents (root only, single filename)
  docs/                              # the product: specs, design docs, wiki, references
  docs/exec-plans/active/            # task ledger (07) + autopilot cards (09)
  docs/_schemas/                     # JSON schemas every status file declares via $schema
  .harness/
    worktrees/{task-id}/             # isolated per-task git worktree           [git-ignored]
    sidecars/{task-id}/              # ephemeral working memory                 [git-ignored]
    coordination/board.sqlite        # claim/liveness CAS store (08)            [git-ignored]
  refs/harness/checkpoints/{task-id}/{n}     # turn checkpoints (durable, survive teardown)
  refs/harness/checkpoints/{task-id}/done    # final success checkpoint

# Per-user, global config + cross-project state (mirrors ~/.openharness via paths.py)
~/.harness/
  settings.json + provider profiles  # config (02)
  data/sessions/                     # session storage
  data/tasks/                        # background task output logs (07)
  memory/{project}-{sha}/MEMORY.md + *.md   # durable memory (11)
  skills/                            # user skills (06)
```

### `WorktreeInfo` (mirrors `swarm/worktree.py`)

`slug`, `path`, `branch`, `original_path`, `created_at`, `agent_id?`. Returned by
`create_worktree`/`list_worktrees`; `agent_id` ties a worktree to the agent that owns it so
`cleanup_stale(active_agent_ids)` can prune orphans.

### Sidecar bundle (`.harness/sidecars/{task-id}/`)

```
db.sqlite                  # per-task structured state
logs/{runner.log,tools.log}
metrics/{timestamp}.json
scratch/                   # tool-output offload, ephemeral (04)
state.json                 # schema-validated; see below
```

### `state.json`

`task_id`, `base_branch`, `created_at`, `last_checkpoint_turn`,
`status` ∈ {`running`,`paused`,`completed`,`failed`}, `parent_checkpoint_ref?`,
`harness_version` (so two harness versions refuse to coexist in one repo).

### `AGENTS.md`

One-line "what this repo is" · pointer to core beliefs/design docs · a short link table
(design docs, product specs, references, current exec-plan, security notes) · optional house
rules (duplicated into a canonical design doc). Links must resolve; the file must stay under
the hard cap.

## Behaviours

### Session start (validator gate)

1. Operator invokes the harness in a repo.
2. Validator runs blocking checks (AGENTS.md present, under hard cap, all links resolve,
   required tree present, every `docs/` JSON valid against its `$schema`, no secret-shaped
   strings) plus any per-repo plug-ins under `.harness/validators/`.
3. Any blocking finding → print findings, exit non-zero, **no worktree or session created**.
4. Warnings/infos only → proceed; warnings are surfaced into the agent's first turn (`#03`).

### Task start

1. Scheduler/operator allocates a new `task-id`; `validate_worktree_slug` runs first — a bad
   slug aborts here, before any filesystem touch.
2. `create_worktree(repo_path, slug, agent_id=…)` creates (or fast-resumes) the worktree from
   the base branch and symlinks common dirs.
3. Runner creates the sidecar bundle and writes `state.json` (status `running`,
   `harness_version` stamped).
4. Runner emits `task.start` on the hook bus (`#13`).

### Successful turn end (checkpoint)

1. Agent/runner signals turn complete.
2. Runner stages modified files in the worktree, commits with a synthetic message carrying the
   turn number.
3. Runner writes the commit SHA to `refs/harness/checkpoints/{task-id}/{n}` (atomic-write the
   ref update).
4. Runner updates `state.json.last_checkpoint_turn` under `exclusive_file_lock`.
5. The commit is *not* pushed; the checkpoint ref is local until task completion.

### Task end (success / failure)

- **Success:** write `.../done` checkpoint → promote final checkpoint to base branch per
  `#09` policy → `state.json.status = completed` → `remove_worktree(slug)` (removes symlinks
  first, then `git worktree remove --force`) and delete the sidecar bundle. Checkpoint refs
  persist.
- **Failure/abort:** write `state.json.status = failed` with reason → delete worktree + sidecars
  → checkpoint refs (including the last successful one) persist so the task is resumable.

### Resume

1. Operator/scheduler invokes resume with a checkpoint ref and a new task-id.
2. Runner creates a new worktree from that ref, fresh sidecars,
   `state.json.parent_checkpoint_ref` = source ref.
3. Task proceeds as a normal fresh task.

### Stale cleanup

`cleanup_stale(active_agent_ids)` lists worktrees, and for each with an `agent_id` not in the
live set, calls `remove_worktree`. With `active_agent_ids=None`, *every* agent-owned worktree
is treated as stale (full sweep). Returns the list of removed slugs.

### Atomic write

`atomic_write_bytes(path, data)` / `atomic_write_text(path, text)`: write to
`{path}.tmp.{uuid}` in the same directory → fsync file → rename over destination → fsync
directory (POSIX). Crash before rename leaves the destination untouched; the temp file is an
orphan cleaned at next task start.

### Exclusive file lock

`with exclusive_file_lock(lock_path):` — acquires an OS-level exclusive lock for the critical
section, releases on exit. POSIX uses `fcntl.flock(LOCK_EX)`; Windows uses
`msvcrt.locking(LK_LOCK, 1)` on the first byte. Unsupported platform → `SwarmLockUnavailableError`.

## Acceptance criteria

### Repo contract & validator (MUST/SHOULD)

1. **MUST** refuse to start a session if `AGENTS.md` is missing, exceeds the hard cap, has a
   dead link, the required tree is incomplete, or any `docs/` JSON fails its `$schema`.
2. **MUST** refuse to start if a secret-shaped string is found under `docs/`, and the finding
   **MUST NOT** echo the secret value.
3. **SHOULD** warn (not block) past the soft cap, on stale exec-plans (>7 days), and on links
   to empty files; **SHOULD** surface all warnings into the agent's first turn.
4. **MUST** treat a git-ignored required folder as missing — the repo, not the working tree, is
   the source of truth.

### Worktree lifecycle (MUST)

5. **MUST** validate every slug via `validate_worktree_slug` *before* any filesystem
   operation; reject `.`/`..`, absolute paths, over-length, and out-of-charset slugs.
6. **MUST** create a fresh worktree per task and refuse to start a task without one; **MUST**
   refuse a second task in an existing worktree.
7. **MUST** fast-resume an existing valid worktree on `create_worktree` rather than erroring or
   duplicating.
8. **MUST** flatten slugs (`/`→`+`) so the worktree directory layout stays flat.
9. **MUST** remove common-dir symlinks before `git worktree remove`, so the shared originals
   are never deleted.
10. **MUST** never reuse a `task-id`.
11. **SHOULD** treat common-dir symlink failure as non-fatal (the worktree still functions).

### Atomic writes & locks (MUST)

12. **MUST** route every harness-initiated write through the atomic-write helper; readers
    **MUST** never observe a torn file.
13. **MUST** leave no `.tmp.{uuid}` file behind on clean exit, and **MUST** clean orphan
    `.tmp.*` files at task start.
14. **MUST** expose cross-platform exclusive locks that auto-release on process exit; **MUST**
    raise (not silently no-op) on a platform where locking is unavailable.

### Checkpoints & resume (MUST/SHOULD)

15. **MUST** write a checkpoint ref on every successful turn end and a `done` ref on success.
16. **MUST** preserve all checkpoint refs after worktree deletion (success *and* failure).
17. **MUST** allow resume from any checkpoint ref into a new worktree with a new task-id, and
    **MUST** record the parent checkpoint ref in the new task's `state.json`.
18. **SHOULD** GC checkpoint refs older than a configurable retention window (default 30 days),
    never deleting `done` refs.

### Concurrency & isolation (MUST)

19. **MUST** support multiple worktrees running concurrently against one base repo with no
    shared sidecar file, DB, or log.
20. **MUST** fail loudly if two processes contend for the same sidecar lock.
21. **MUST** stamp `harness_version` in `state.json`; mismatched harness versions **MUST**
    refuse to coexist in one repo.

## Acceptance scenarios

```gherkin
Scenario: Path-traversal slug is rejected before touching disk
  Given a task-id "../../etc/passwd"
  When the runner starts the task
  Then validate_worktree_slug raises ValueError
  And no directory is created under .harness/worktrees/
  And task start aborts with a blocking error.

Scenario: create_worktree fast-resumes an existing worktree
  Given a worktree for slug "T1" already exists and is a valid git worktree
  When the runner calls create_worktree(repo, "T1")
  Then no "git worktree add" is run
  And the returned WorktreeInfo points at the existing path and branch.

Scenario: Teardown removes symlinks before removing the worktree
  Given worktree "T1" has node_modules symlinked to the main repo
  When the runner removes worktree "T1"
  Then the node_modules symlink is unlinked first
  And the main repo's node_modules still exists
  And git worktree remove --force succeeds.

Scenario: Atomic write survives a crash mid-write
  Given a file F containing "v1"
  When the harness writes "v2" via atomic_write_text and crashes after the temp write but before rename
  Then F still contains "v1"
  And exactly one {F}.tmp.{uuid} orphan exists
  And that orphan is deleted at the next task start.

Scenario: Checkpoint survives worktree teardown
  Given task T1 completed 3 turns and then succeeded
  When the runner tears down T1
  Then .harness/worktrees/T1/ and .harness/sidecars/T1/ no longer exist
  And refs/harness/checkpoints/T1/1, /2, /3, and /done all still resolve.

Scenario: Resume from a checkpoint into a new task
  Given task T1 ended (success or failure) at checkpoint 3
  When the operator resumes from refs/harness/checkpoints/T1/3 as task T2
  Then a fresh worktree exists at .harness/worktrees/T2/
  And T2's state.json.parent_checkpoint_ref points at T1/3
  And T2's working tree byte-matches that checkpoint commit.

Scenario: cleanup_stale prunes orphaned worktrees
  Given worktrees owned by agents A1 (dead) and A2 (alive)
  When the runner calls cleanup_stale(active_agent_ids={"A2"})
  Then A1's worktree is removed and its slug returned
  And A2's worktree is untouched.

Scenario: Cross-platform lock blocks a second writer
  Given process A holds exclusive_file_lock on sidecars/T1/state.json
  When process B enters exclusive_file_lock on the same path
  Then B blocks until A's context exits
  And A's critical section completes without interleaving.

Scenario: Mismatched harness versions refuse to coexist
  Given a repo with an active task written by harness version X
  When a runner of harness version Y starts a task in the same repo
  Then the runner refuses with a blocking version-mismatch error.
```

## Tests

- `test_validate_slug_rejects_traversal` — `.`/`..`/absolute/over-length/out-of-charset all raise.
- `test_validate_slug_runs_before_filesystem_touch` — no dir created on a bad slug.
- `test_flatten_slug_replaces_slash_with_plus` — flat layout invariant.
- `test_create_worktree_happy_path` — worktree + branch + symlinks created.
- `test_create_worktree_fast_resume` — existing valid worktree returned, no `git worktree add`.
- `test_create_worktree_minus_B_resets_orphan_branch` — orphan branch from prior remove handled.
- `test_symlink_common_dirs_non_fatal_on_failure` — disk-full/unsupported fs tolerated.
- `test_remove_worktree_removes_symlinks_first` — shared originals preserved.
- `test_remove_worktree_returns_false_when_absent` — idempotent remove.
- `test_list_worktrees_recovers_slug_branch_origin` — `+`→`/` restore, branch + common-dir recovery.
- `test_cleanup_stale_prunes_only_dead_agents` — active set respected.
- `test_cleanup_stale_none_sweeps_all_agent_owned` — full-sweep semantics.
- `test_task_id_never_reused` — allocator forbids collision.
- `test_second_task_in_same_worktree_refused` — concurrency safety.
- `test_two_concurrent_tasks_isolated_sidecars` — no shared db/logs.
- `test_atomic_write_no_partial_file_visible` — readers never see torn writes.
- `test_atomic_write_crash_preserves_old` — durability under crash.
- `test_no_tmp_left_on_clean_exit` / `test_orphan_tmp_cleaned_at_task_start` — hygiene.
- `test_exclusive_lock_blocks_second_writer` — locking primitive works.
- `test_exclusive_lock_released_on_process_exit` — no zombie locks.
- `test_exclusive_lock_raises_on_unsupported_platform` — no silent degrade.
- `test_checkpoint_ref_per_turn` / `test_done_checkpoint_on_success` — durability per turn.
- `test_checkpoints_survive_teardown` — success and failure paths.
- `test_resume_creates_new_worktree_records_parent` — lineage tracked.
- `test_resumed_tree_byte_matches_checkpoint` — bytes preserved.
- `test_checkpoint_gc_respects_window_keeps_done` — GC policy honoured.
- `test_validator_blocks_missing_agents_md` / `_oversized` / `_dead_link` / `_invalid_json` / `_secret`.
- `test_secret_finding_redacts_value` — finding never echoes the secret.
- `test_git_ignored_required_folder_treated_missing` — repo-not-worktree truth.
- `test_harness_version_mismatch_refused` — version stamp enforced.

## Edge cases

- **Disk full mid-checkpoint.** Atomic write fails → turn not marked complete → runner emits a
  hook error → the agent's next turn sees the failure (no silent half-checkpoint).
- **Worktree path too long on Windows.** Refuse at task start with a blocking error rather than
  failing opaquely mid-run.
- **Operator manually deletes a worktree mid-task.** Detected on next turn end (state.json
  missing/stale) → task aborts → checkpoints up to that point survive → resumable.
- **Network filesystem (NFS/SMB) hosts the repo.** `fsync` and advisory-lock semantics are
  weaker; runner emits a warning at task start that durability/locking guarantees are degraded.
- **`git worktree add -B` colliding with a real branch.** `-B` force-resets the generated
  `worktree-{slug}` branch; never targets a human branch because the name is namespaced.
- **Symlink loop or pre-existing common dir in the worktree.** `_symlink_common_dirs` skips any
  destination that already exists or is already a symlink (no clobber).
- **Submodules.** Worktrees do not check out submodules by default; deferred.
- **Two harness versions in one repo.** `harness_version` in `state.json` makes the mismatch a
  hard refusal, not silent corruption.

## Open questions

- Whether checkpoint commits should be GPG-signed so tampering is visible in the ref history.
- Whether to expose checkpoints behind a human-browsable "branch view" or keep them hidden under
  `refs/harness/` (current default: hidden).
- Whether sidecar bundles should be encrypted at rest by default (currently not).
- Whether `AGENTS.md` line counts should exclude code fences/tables, and whether the hard cap
  should be byte-based for CJK-heavy repos.
- Whether secret-detection patterns are operator-configurable or fixed (current default: fixed
  list with override via a security config file).

## Out of scope

- The turn loop and orientation prompt that run *inside* a worktree (→ `#03`).
- Who is allowed to claim a worktree, the CAS coordination store, heartbeats, and recovery
  (→ `#08`).
- Network sandboxing and path-permission enforcement on writes (→ `#13`).
- Cron scheduling and the exec-plan ledger semantics (→ `#07`).
- The promotion/merge policy taking a finished worktree to mainline (→ `#09`).
- Memory tiering and consolidation (→ `#11`).
- Multi-host distribution of worktrees (deferred — single-host only in v1).
```
