# Deferred — making the filesystem/git layer async

**Origin:** design decision during PR 3. **Status:** deferred (sync ships in spec 01). **Effort:** ~1 PR.

## The decision that was made

The spec-01 filesystem/git layer is **synchronous**. These modules do blocking I/O
(file reads/writes, `fsync`, `fcntl`/`msvcrt` locks) or blocking `subprocess.run`
git calls:

| Module | Blocking work |
|---|---|
| `utils/fs.py` | file write + `fsync` + rename |
| `utils/file_lock.py` | `fcntl.flock` / `msvcrt.locking` |
| `utils/git.py` (`run_git`) | `subprocess.run(["git", …])` |
| `swarm/_worktree.py` (`WorktreeManager`) | git via `run_git` |
| `state/sidecar.py` | file I/O + lock |
| `state/checkpoints.py` | git via `run_git` |
| `services/repo_validator.py` | file I/O + `git check-ignore` |
| `config/paths.py` | **none** — pure value object, no migration needed |

**Why sync was chosen:** these are *coarse lifecycle operations* (task start/end,
checkpoint per turn, session start) — not the per-token hot path. Sync code is much
simpler to test (no async ceremony), and it kept spec 01 focused. OpenHarness made
these async (`asyncio.create_subprocess_exec`); dream deliberately diverged.

## The tension to resolve

The project's stated rule (HANDOFF #3) is **"async-first; the sync facade in
`dream.sync` is thin."** The engine (spec 03) is async. Calling a blocking sync
function directly from the async event loop **blocks the loop** for the duration of
the git/file call. So before the engine drives worktrees/checkpoints/sidecars, this
layer needs an async story.

## Options

### A. `asyncio.to_thread` wrappers (recommended)

Keep the well-tested sync core; expose async entry points that offload the blocking
call to a thread:

```python
# e.g. dream/swarm/worktree_async.py  (or methods on an async facade)
async def create_worktree(self, slug, *, agent_id=None, start_point="HEAD"):
    return await asyncio.to_thread(
        self._sync.create_worktree, slug, agent_id=agent_id, start_point=start_point
    )
```

- **Pros:** zero rewrite of tested logic; blocking I/O runs off the event loop;
  trivial. `asyncio.to_thread` is *exactly* designed for blocking I/O like this.
- **Cons:** a thread-pool hop per call (negligible for coarse lifecycle ops).

### B. Native async rewrite

Rewrite `run_git` to `asyncio.create_subprocess_exec` (OpenHarness style) and file
I/O to threads/`anyio`. More invasive; only worth it if profiling shows thread-pool
overhead matters — which it won't at task-lifecycle frequency.

### C. Leave sync, call at boundaries

Because these run at task setup/teardown (not per token), the engine *could* call
them synchronously at well-defined await boundaries. Simplest, but violates the
async-first rule and risks a stray long git call stalling the loop.

## Recommendation

**Option A.** Keep the sync core as the source of truth (already 95–100% tested);
add thin `await asyncio.to_thread(...)` wrappers where the async engine consumes
them. Decide placement when the engine lands:
- async methods alongside the sync ones, or
- an async facade module, with `dream.sync` as the thin *sync* surface per HANDOFF.

`config/paths.py` needs nothing (pure). `utils/file_lock.py` is the one to watch —
an OS advisory lock held across an `await` is fine, but document that the lock must
be acquired and released **within the same thread** (which `to_thread` guarantees
per call; don't split acquire/release across two `to_thread` hops).

## Acceptance criteria (when picked up)

- **MUST** expose async entry points for every blocking lifecycle op the engine
  calls (worktree create/remove, checkpoint write/done, sidecar read/update,
  validate_repo).
- **MUST NOT** block the event loop for the duration of a git/file call.
- **SHOULD** keep the sync functions as the tested core; async wrappers delegate.
- **MUST** keep lock acquire+release on a single thread.

## Why deferred

Nothing depends on it until the **engine (spec 03)** drives these ops. Adding async
wrappers now would be speculative surface with no caller. Build it the moment spec
03 needs to `await` a worktree/checkpoint — that's when the seam becomes real.
