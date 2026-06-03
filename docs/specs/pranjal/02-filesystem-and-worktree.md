# 02 — Filesystem & Per-Worktree Isolation

**One-liner:** Every task runs in its own ephemeral git worktree with its own DB, logs, metrics, and sandbox. Worktrees are disposable; checkpoints are durable.

**Sources:** [OAI], [LC] · taxonomy §10, §18, §20

---

## Why this matters

The harness will run many tasks concurrently (cron jobs, user-initiated work, dream-phase curators) against the same repo. If they share state — a SQLite file, a log directory, a working tree — they will corrupt each other in non-obvious ways. The solution every mature harness converges on is per-task isolation at the filesystem layer: a fresh git worktree per task, with all sidecar state colocated and removed together.

The second concern is durability under failure. Long-running tasks die — the model goes silent, the host reboots, a tool hangs. We want to resume from the last known good state, not from "session start." That means checkpoints, frequent and cheap, named in a way that survives worktree teardown.

The third concern is cross-platform safety. We will run on Windows, macOS, and Linux. File locks, atomic writes, and path semantics differ; we hide that behind a thin layer so the agent never sees it.

## Scope

**In:** worktree lifecycle, sidecar layout, atomic-write contract, cross-platform file locking, checkpoint refs, resume-from-checkpoint, teardown.

**Out:** what runs *inside* the worktree (→ #03, #06); network sandboxing (→ #08); substrate-level VM/container isolation (→ #11); cron scheduling (→ #09).

## Key decisions (assumed defaults)

1. **One worktree per task.** Tasks are the unit of isolation. A "session" in #03 runs inside one worktree.
2. **Worktrees live under `.harness/worktrees/{task-id}/`.** Inside the repo, git-ignored, so a repo browser sees them but git tracking ignores them.
3. **Each worktree gets its own sidecar bundle** colocated in `.harness/sidecars/{task-id}/`:
   - `db.sqlite` for per-task structured state
   - `logs/` for append-only logs
   - `metrics/` for periodic metric dumps
   - `scratch/` for tool-output offload (#04)
4. **Atomic writes via temp-then-rename.** Every write the harness performs uses `write → fsync → rename` so other processes never see a half-written file. Crash-during-write leaves the prior version intact.
5. **Cross-platform locking.** A thin wrapper exposes `acquire`/`release`/`try_acquire` over `fcntl` on POSIX and `LockFileEx` on Windows. Locks are per-file, advisory, and the wrapper guarantees release on process exit.
6. **Checkpoints are git refs.** After every successful turn end, the runner commits the working tree of the worktree to a shadow ref under `refs/harness/checkpoints/{task-id}/{n}`. These refs are not branches — they don't show up in `git branch -a` by default.
7. **Teardown deletes the worktree and sidecars together.** Checkpoint refs survive teardown.
8. **Resume from any checkpoint.** Operator (or scheduler) can spawn a new worktree at any checkpoint ref; the new task gets a new id but inherits the checkpoint as its base.
9. **Concurrency is per-worktree.** Two tasks in two worktrees can run in parallel against the same repo; two tasks in the *same* worktree are forbidden.
10. **No worktree-to-worktree communication.** Tasks coordinate only through committed state on the base branch.

## Artefact shapes

### Worktree layout

```
.harness/
  worktrees/{task-id}/         # actual git worktree
  sidecars/{task-id}/
    db.sqlite
    logs/
      runner.log
      tools.log
    metrics/
      {timestamp}.json
    scratch/                   # tool-output offload, ephemeral
    state.json                 # current task state, schema-validated
```

### Checkpoint ref naming

- `refs/harness/checkpoints/{task-id}/{turn-number}` — one per successful turn end.
- `refs/harness/checkpoints/{task-id}/done` — the final checkpoint, written on success.

### State file (`sidecars/{task-id}/state.json`)

- `task_id`
- `base_branch`
- `created_at`
- `last_checkpoint_turn`
- `status` — one of `running`, `paused`, `completed`, `failed`
- `parent_checkpoint_ref` — if this task resumed from a checkpoint

## Behaviours

### Task start

1. Scheduler (or operator) allocates a new `task-id`.
2. Runner creates the worktree from the configured base branch.
3. Runner creates the sidecar bundle.
4. Runner writes `state.json` with status `running`.
5. Runner emits `task.start` on the hook bus (#12).

### Successful turn end

1. Agent or runner signals turn complete.
2. Runner stages any modified files in the worktree, commits with a synthetic message including turn number.
3. Runner writes the commit's SHA to `refs/harness/checkpoints/{task-id}/{n}`.
4. Runner updates `state.json.last_checkpoint_turn`.
5. Runner does *not* push the commit anywhere; the checkpoint ref is local to the worktree's repo until task completion.

### Task end (success)

1. Runner writes the final checkpoint as `.../done`.
2. Runner merges or PR-promotes the final checkpoint to the base branch (policy is per-task; see #09).
3. Runner sets `state.json.status = completed`.
4. Runner deletes the worktree and sidecar bundle.
5. Checkpoint refs persist.

### Task end (failure or abort)

1. Runner writes a final `state.json.status = failed` with reason.
2. Runner deletes the worktree and sidecar bundle.
3. Checkpoint refs persist — including the *last* successful one — so the task can be resumed.

### Resume

1. Operator (or scheduler) invokes resume with a checkpoint ref and an optional new task id.
2. Runner creates a new worktree from that ref.
3. New worktree gets fresh sidecars; `state.json.parent_checkpoint_ref` records the lineage.
4. Task proceeds normally.

### Atomic write

The atomic-write helper takes `(path, bytes)`:
1. Writes `bytes` to `{path}.tmp.{uuid}` in the same directory.
2. `fsync`s the file.
3. Renames the temp file over the destination.
4. `fsync`s the directory (POSIX) or relies on `MoveFileEx` with `MOVEFILE_WRITE_THROUGH` (Windows).

### File locking

The lock helper exposes:
- `acquire(path, mode=exclusive|shared, timeout=None)`
- `release(handle)`
- `try_acquire(...)` — returns `None` on contention.

Locks released automatically when the process exits even if `release` is not called.

## Acceptance criteria

### Worktree lifecycle (MUST)

1. **MUST** create a fresh worktree per task; refuse to start a task without one.
2. **MUST** colocate each worktree's sidecars under a single `sidecars/{task-id}/` directory.
3. **MUST** delete the worktree on task end (success, failure, or abort).
4. **MUST** delete the sidecar bundle on task end.
5. **MUST** never reuse a `task-id` across tasks.
6. **MUST** refuse to start a second task in an existing worktree.

### Atomic writes & locks (MUST)

7. **MUST** route every harness-initiated write through the atomic-write helper.
8. **MUST** never leave a `.tmp.{uuid}` file behind on a clean exit.
9. **MUST** clean up orphan `.tmp.*` files older than the worktree itself at task start.
10. **MUST** expose cross-platform locks that auto-release on process exit.

### Checkpoints (MUST/SHOULD)

11. **MUST** create a checkpoint ref on every successful turn end.
12. **MUST** preserve checkpoint refs after worktree deletion.
13. **MUST** create a `done` checkpoint on successful task end.
14. **SHOULD** garbage-collect checkpoint refs older than a configurable retention window (default: 30 days), except `done` refs.

### Resume (MUST)

15. **MUST** allow resuming from any checkpoint ref into a new worktree with a new task id.
16. **MUST** record the parent checkpoint ref in the new task's state file.
17. **MUST** make the resumed task behave identically to a fresh task except for its starting tree.

### Concurrency (MUST)

18. **MUST** support multiple worktrees running concurrently against the same base repo.
19. **MUST** ensure no two worktrees share a sidecar file, DB, or log.
20. **MUST** fail loudly if two processes try to lock the same sidecar file simultaneously.

## Acceptance scenarios

```gherkin
Scenario: Task start creates a fresh worktree
  Given a clean repo with no .harness/worktrees/ entries
  When the operator starts task T1
  Then a worktree exists at .harness/worktrees/T1/
  And a sidecar bundle exists at .harness/sidecars/T1/
  And state.json has status "running".

Scenario: Two concurrent tasks have isolated sidecars
  Given two tasks T1 and T2 starting simultaneously
  When both runners initialise their sidecars
  Then T1's db.sqlite and T2's db.sqlite are different files
  And neither task can read the other's logs.

Scenario: Atomic write survives a crash mid-write
  Given a file F containing "v1"
  When the harness begins to write "v2" to F
  And the process crashes after writing the temp file but before rename
  Then F still contains "v1"
  And a {F}.tmp.{uuid} file exists.

Scenario: Orphan temp files cleaned at task start
  Given .harness/sidecars/ contains a {F}.tmp.{uuid} from a prior crash
  When task T1 starts
  Then the orphan temp file is deleted
  And an info-level event records the cleanup.

Scenario: Checkpoint ref created per successful turn end
  Given task T1 has completed 3 turns
  When the runner finalises each turn
  Then refs/harness/checkpoints/T1/1, /2, /3 all point to commits
  And state.json.last_checkpoint_turn is 3.

Scenario: Task end deletes worktree but preserves checkpoints
  Given task T1 has completed successfully
  When the runner tears down T1
  Then .harness/worktrees/T1/ no longer exists
  And .harness/sidecars/T1/ no longer exists
  And refs/harness/checkpoints/T1/done still exists.

Scenario: Resume from checkpoint into new task
  Given task T1 ended at checkpoint 3
  When the operator resumes from refs/harness/checkpoints/T1/3 as task T2
  Then a new worktree exists at .harness/worktrees/T2/
  And T2's state.json.parent_checkpoint_ref points at T1/3
  And T2's working tree matches the contents of that checkpoint commit.

Scenario: Cross-platform lock blocks second writer
  Given process A holds an exclusive lock on sidecars/T1/state.json
  When process B calls try_acquire on the same file
  Then the call returns "no handle"
  And A's lock is still held.
```

## Tests

- `test_task_start_creates_worktree` — basic happy path.
- `test_task_start_creates_sidecar_bundle` — sidecars exist alongside worktree.
- `test_task_id_never_reused` — id allocator forbids collision.
- `test_second_task_in_same_worktree_refused` — concurrency safety.
- `test_two_concurrent_tasks_have_isolated_sidecars` — no shared files.
- `test_two_concurrent_tasks_have_separate_dbs` — explicit DB isolation.
- `test_atomic_write_no_partial_file_visible` — readers never see torn writes.
- `test_atomic_write_crash_mid_write_preserves_old` — durability under crash.
- `test_no_tmp_files_left_on_clean_exit` — hygiene.
- `test_orphan_tmp_files_cleaned_at_task_start` — recovery hygiene.
- `test_cross_platform_lock_blocks_second_writer` — locking primitive works.
- `test_lock_released_on_process_exit` — no zombie locks.
- `test_checkpoint_ref_created_per_turn` — durability per turn.
- `test_done_checkpoint_created_on_success` — final marker.
- `test_task_end_deletes_worktree` — teardown.
- `test_task_end_deletes_sidecar_bundle` — teardown.
- `test_task_end_preserves_checkpoint_refs` — durability across teardown.
- `test_checkpoint_gc_respects_retention_window` — GC policy honoured.
- `test_checkpoint_gc_never_deletes_done_refs` — final markers protected.
- `test_resume_from_checkpoint_creates_new_worktree` — resume happy path.
- `test_resume_records_parent_checkpoint_ref` — lineage tracked.
- `test_resumed_tree_matches_checkpoint_contents` — bytes preserved.
- `test_failed_task_still_preserves_last_checkpoint` — failure recoverable.

## Edge cases

- **Disk full mid-checkpoint.** Atomic write fails; turn is not marked complete; runner emits a hook error and the agent's next turn sees the failure.
- **Worktree path too long on Windows.** Runner refuses to create the worktree, surfaces a blocking error at task start instead of failing later.
- **Operator manually deletes a worktree mid-task.** Runner detects on next turn end (state.json missing or stale) and aborts the task; checkpoints up to that point survive.
- **Two different harness versions running concurrently in the same repo.** Sidecar layout includes a version marker; mismatched versions refuse to coexist.
- **Network filesystem (NFS/SMB) hosting the repo.** `fsync` semantics are weaker; runner emits a warning at task start that durability guarantees are degraded.
- **Submodules.** Worktrees do not include submodule checkouts by default; submodule support is deferred.

## Open questions

- Whether checkpoint commits should be signed (operator's GPG key) to make tampering visible.
- Whether to expose checkpoints as a normal-looking "branch view" for human browsing, or keep them hidden under `refs/harness/`.
- Whether sidecar bundles should be encrypted at rest by default (currently not).

## Out of scope

- Network sandbox details (→ #08).
- Substrate-level VM/container isolation (→ #11).
- Git workflow for promoting completed task work to mainline (→ #09).
- Multi-host distribution of worktrees (deferred — single-host only in v1).
