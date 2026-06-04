# Spec 01 — Repo as System of Record & Filesystem Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the durable-state foundation of the harness — atomic crash-safe writes, cross-platform locks, the two storage roots, per-task git-worktree isolation, sidecars, checkpoints, and the session-start validator — so every later spec can assume "saved means saved" and "tasks never interfere."

**Architecture:** Pure leaf utilities first (no dependencies, no git), then the path map, then git-backed worktree/checkpoint machinery, then the validator. Each layer is a separate PR that merges green on its own. The repo (git) is the only system of record; everything under `.dream/` is disposable now-state.

**Tech Stack:** Python 3.13, pytest (`asyncio_mode=auto`), pydantic v2, stdlib `os`/`fcntl`/`msvcrt`/`subprocess` (git), `uv` for env. Reference implementation to lift from: `/tmp/OpenHarness/src/openharness/` (`utils/fs.py`, `utils/file_lock.py`, `config/paths.py`, `swarm/worktree.py`).

---

## How to read this plan

This plan is **PR-structured and smallest-first**, per the request *"the smallest change that could alter the system, do it, then let me iterate."*

- **PR 1 is fully detailed** with bite-sized RED→GREEN→commit steps and complete code — it is ready to execute now.
- **PR 2–8 are concrete design outlines** (exact files, signatures, test names, acceptance, dependencies) — enough to iterate on the *structure*. Each is expanded into full bite-sized steps **when we reach it**, after you've reviewed the results of the previous PR. This is a deliberate, requested deviation from "full code in every step": we don't write 40 tests' worth of code before you've confirmed the shape.

**Iterate here first.** Two things to confirm before any code (see "Open decisions").

---

## Open decisions

1. **`.dream/` vs `.harness/` naming.** ✅ **RESOLVED → `.dream/`** (`~/.dream/`, `refs/dream/checkpoints/`), to match package identity. Constants: `DREAM_DIRNAME=".dream"`, `CHECKPOINT_REF_PREFIX="refs/dream/checkpoints"`.
2. **`harness_version` source.** ⏸ Deferred (revisit at PR 4 / sidecar). Default for now: read `dream.__version__` (`"0.1.0"`).
3. **Pure `DreamPaths` vs side-effecting getters.** ⏸ Deferred (revisit when building paths). Default for now: *pure value object* + one explicit `.ensure()`.

---

## File structure (what each file owns)

| File | Responsibility | PR |
|---|---|---|
| `src/dream/utils/fs.py` | Atomic writes (`temp → fsync → rename`) + orphan-temp sweep | 1 |
| `src/dream/utils/file_lock.py` | Cross-platform exclusive file lock context manager | 2 |
| `src/dream/config/paths.py` | The two storage roots and every derived path (`DreamPaths`) | 3 |
| `.gitignore` | Add `.dream/` so now-state is never committed | 3 |
| `src/dream/swarm/_worktree.py` | Slug validation/flatten (PR4) + worktree lifecycle (PR5) | 4, 5 |
| `src/dream/state/sidecar.py` | Sidecar bundle layout + `TaskState` (`state.json`) model | 6 |
| `src/dream/state/checkpoints.py` | Checkpoint git refs, survive-teardown, resume | 7 |
| `src/dream/services/repo_validator.py` | `AGENTS.md` + session-start validator, 3 severities | 8 |

Test files mirror the tree: `tests/test_utils/`, `tests/test_config/`, `tests/test_swarm/`, `tests/test_state/`, `tests/test_services/` (all already exist with `__init__.py`).

---

## PR sequence (the map to iterate on)

Each PR is independently mergeable, dependency-ordered, and turns a named subset of spec-01 tests green.

| PR | Title | New deps | Spec criteria covered | ~Tests |
|---|---|---|---|---|
| **1** | **Filesystem primitives** — atomic writes + file lock + paths + `.gitignore` | none | 12, 13, 14, decision 1 (two roots) | 26 |
| 2 | Slug validation + flatten | PR1 | 5, 8, scenario "path-traversal rejected" | 4 |
| 3 | Worktree lifecycle (create/resume/remove/list/cleanup) | git, PR1, PR2 | 6, 7, 9, 11, 19, scenarios fast-resume / teardown-symlinks / cleanup_stale | 10 |
| 4 | Sidecar bundle + `state.json` | PR1 | 8 (decision), 20, 21, harness-version | 5 |
| 5 | Checkpoints + resume | git, PR1, PR3 | 10, 15, 16, 17, 18, scenarios checkpoint-survives / resume | 6 |
| 6 | Session-start validator + `AGENTS.md` | PR1 | 1, 2, 3, 4, scenarios missing/oversized/secret/stale | 8 |

**PR 1 bundles the three dependency-free leaf utilities** (atomic writes, file lock, paths). They share no git/worktree machinery, are pure plumbing, and land together as one reviewable "primitives" PR. Within PR 1 they're built in TDD order as **Part A → B → C**, each its own commit; the PR opens after Part C. The detailed steps below under "PR 1: Atomic file writes / PR 2: Exclusive file lock / PR 3: Storage paths" are **Parts A / B / C of this PR 1**. Sections previously numbered PR 4–8 are now **PR 2–6**.

---

## Prerequisite: environment setup (one-time, not a PR)

- [ ] **Recreate the venv** (we reverted it):

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -c "import dream; print('OK', dream.__version__)"
```

Expected: `OK 0.1.0`

- [ ] **Create the PR 1 branch:**

```bash
git checkout -b pr1-primitives
```

---

## PR 1: Filesystem primitives (atomic writes + file lock + paths)

**Branch:** `pr1-primitives` — three dependency-free leaf utilities on one branch, built TDD-order as Part A → B → C, one commit each, PR opened after Part C.

### Part A: Atomic file writes

**Files:**
- Modify: `src/dream/utils/fs.py` (currently a one-line docstring stub)
- Test: `tests/test_utils/test_fs.py` (create)

**What it delivers:** `atomic_write_bytes`, `atomic_write_text`, and `clean_orphan_temp_files`. The temp-then-rename dance means a reader never sees a torn file and a crash mid-write leaves the previous version fully intact. Implements spec criterion 12 (atomic writes) and 13 (no temp left on clean exit; orphan `.tmp.*` swept). Temp file pattern follows the spec: `{name}.tmp.{uuid}`.

**Why this design:** lifted from OpenHarness `utils/fs.py` (proven), adapted to the spec's `.tmp.{uuid}` naming and given an explicit `clean_orphan_temp_files` sweeper (criterion 13's "clean orphan `.tmp.*` files at task start").

---

### Task 1.1 — `atomic_write_bytes` / `atomic_write_text`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_utils/test_fs.py`:

```python
"""Spec 01 — atomic writes (temp -> fsync -> rename) and orphan-temp cleanup.

Invariant: a reader never observes a torn file, and a crash mid-write leaves the
previous version fully intact. Temp files match the spec pattern `{name}.tmp.{uuid}`.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from dream.utils.fs import atomic_write_bytes, atomic_write_text, clean_orphan_temp_files


def test_atomic_write_bytes_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "héllo")
    assert target.read_text(encoding="utf-8") == "héllo"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    assert target.read_text() == "v2"


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.txt"
    atomic_write_text(target, "x")
    assert target.read_text() == "x"


def test_no_temp_file_left_after_success(tmp_path: Path) -> None:
    atomic_write_text(tmp_path / "f.txt", "x")
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_failure_at_rename_preserves_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "v1")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("dream.utils.fs.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "v2")

    assert target.read_text() == "v1"            # old version intact
    assert list(tmp_path.glob("*.tmp.*")) == []  # temp cleaned by error path


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_atomic_write_mode_applied(tmp_path: Path) -> None:
    target = tmp_path / "secret"
    atomic_write_bytes(target, b"x", mode=0o600)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_utils/test_fs.py -q`
Expected: collection ERROR — `ImportError: cannot import name 'atomic_write_bytes' from 'dream.utils.fs'`

- [ ] **Step 3: Write the implementation**

Replace `src/dream/utils/fs.py` with:

```python
"""Atomic file-write helpers (spec 01).

Every harness-initiated write goes through here: write to a same-directory temp
file, fsync it, then `os.replace` it over the destination. `os.replace` is atomic
on POSIX and (since Python 3.3) Windows, so a concurrent reader sees either the
old file or the new one, never a half-written one. A crash before the rename
leaves the destination untouched and an orphan `{name}.tmp.{uuid}` that
`clean_orphan_temp_files` sweeps at task start.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "clean_orphan_temp_files"]

_TMP_GLOB = "*.tmp.*"


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes, *, mode: int | None = None) -> None:
    """Write ``data`` to ``path`` atomically (temp -> fsync -> rename)."""
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp.{uuid.uuid4().hex}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(tmp, mode)
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Text variant of :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def clean_orphan_temp_files(directory: str | os.PathLike[str]) -> list[Path]:
    """Remove leftover `*.tmp.*` files from interrupted writes; return removed paths."""
    d = Path(directory)
    removed: list[Path] = []
    if not d.is_dir():
        return removed
    for p in sorted(d.glob(_TMP_GLOB)):
        with contextlib.suppress(OSError):
            p.unlink()
            removed.append(p)
    return removed


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so the rename is durable (POSIX only; no-op elsewhere)."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_utils/test_fs.py -q`
Expected: `7 passed` (the `clean_orphan_temp_files`-specific tests come in Task 1.2; the import now resolves and all 7 above pass).

- [ ] **Step 5: Commit**

```bash
git add src/dream/utils/fs.py tests/test_utils/test_fs.py
git commit -m "feat(fs): atomic write helpers (temp->fsync->rename)"
```

---

### Task 1.2 — `clean_orphan_temp_files` sweep

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_utils/test_fs.py`:

```python
def test_clean_orphan_temp_files_removes_only_temps(tmp_path: Path) -> None:
    real = tmp_path / "state.json"
    real.write_text("{}")
    orphan = tmp_path / "state.json.tmp.deadbeef"
    orphan.write_text("partial")

    removed = clean_orphan_temp_files(tmp_path)

    assert not orphan.exists()
    assert real.read_text() == "{}"
    assert orphan in removed


def test_clean_orphan_temp_files_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert clean_orphan_temp_files(tmp_path) == []


def test_clean_orphan_temp_files_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert clean_orphan_temp_files(tmp_path / "nope") == []
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_utils/test_fs.py -q`
Expected: `10 passed` (implementation from Task 1.1 already covers these — this task proves the sweep behavior is correct and locks it with tests).

- [ ] **Step 3: Run the full suite + coverage gate**

Run: `.venv/bin/python -m pytest tests/test_utils/test_fs.py --cov=src/dream/utils/fs --cov-report=term-missing -q`
Expected: `10 passed`, coverage on `fs.py` ≥ 80% (target 100%).

- [ ] **Step 4: Commit**

```bash
git add tests/test_utils/test_fs.py
git commit -m "test(fs): lock orphan-temp sweep behavior"
```

- [ ] **Step 5: Continue to Part B (file lock) — do NOT open the PR yet**

Part A (atomic writes) is committed on `pr1-primitives`. PR 1 also includes file lock (Part B) and paths (Part C); the PR opens after Part C. Proceed to Part B below.

---

## PR 1 · Part B: Exclusive file lock (expand at execution)

**Branch:** `pr1-primitives` (same branch) · **Files:** `src/dream/utils/file_lock.py`, `tests/test_utils/test_file_lock.py`

**Public API:**
```python
class LockError(RuntimeError): ...
class LockUnavailableError(LockError): ...

@contextmanager
def exclusive_file_lock(lock_path: Path, *, platform: str | None = None) -> Iterator[None]: ...
```
POSIX → `fcntl.flock(LOCK_EX)`; Windows → `msvcrt.locking`; unknown `platform` → raise `LockUnavailableError`. `platform=None` auto-detects from `sys.platform`; the injectable param makes the "unsupported platform raises" rule testable. (Renamed from spec/OpenHarness `SwarmLockUnavailableError` — "swarm" is not lock-specific.)

**Tests:** `test_lock_acquire_and_release`, `test_lock_creates_parent_dir`, `test_lock_serializes_concurrent_writers` (two threads, asserts order `a-start, a-end, b-start, b-end`), `test_lock_raises_on_unsupported_platform`.

**Acceptance:** criterion 14 + scenario "Cross-platform lock blocks a second writer."

---

## PR 1 · Part C: Storage paths — the two roots (expand at execution)

**Branch:** `pr1-primitives` (same branch) · **Files:** `src/dream/config/paths.py`, `.gitignore`, `tests/test_config/test_paths.py`

Naming resolved: **`.dream`**. After this part: push `pr1-primitives` and open the PR (`gh pr create --title "PR1: filesystem primitives (atomic writes + file lock + paths)"`). **Then STOP — review before PR 2.**

**Public API:** frozen dataclass `DreamPaths(repo: Path, home: Path)` — a *pure value object*; reading a path property touches no filesystem; the only side effect is `.ensure()`.
```python
DreamPaths.resolve(repo, *, home=None, env=None) -> DreamPaths   # home: arg > $DREAM_HOME > ~/.dream
# repo-side now-state: .dream_dir, .worktrees_dir, .sidecars_dir, .coordination_dir, .coordination_board
# repo-side of-record: .docs_dir, .exec_plans_active, .schemas_dir, .agents_md
# per-task: .worktree(task_id), .sidecar(task_id), .checkpoint_ref(task_id, n)  # refs/dream/checkpoints/{t}/{n}
# home-side: .settings_file, .sessions_dir, .tasks_dir, .memory_dir, .skills_dir
# side effect: .ensure() -> creates now-state dirs only (never docs/), returns self, idempotent
```
Module constants: `DREAM_DIRNAME=".dream"`, `DREAM_HOME_ENV="DREAM_HOME"`, `CHECKPOINT_REF_PREFIX="refs/dream/checkpoints"`.

**`.gitignore` change:** add `.dream/` (now-state must never be committed — spec decision 1).

**Tests (~12):** resolve precedence (arg/env/default), `repo` made absolute, repo-side now-state layout, of-record layout, per-task paths, checkpoint-ref format (int and `"done"`), home-side paths, `reading_properties_creates_nothing`, `ensure_creates_now_state_dirs_only`, `ensure_is_idempotent`, frozen.

**Acceptance:** decision 1 (two roots, of-record vs now split) + the storage-layout artefact.

---

## PR 2: Slug validation + flatten (outline)

**Branch:** `pr2-slug` · **Files:** `src/dream/swarm/_worktree.py` (start), `tests/test_swarm/test_worktree_slug.py`

**Public API:**
```python
def validate_worktree_slug(slug: str) -> None: ...   # raises ValueError on bad slug; security boundary
def flatten_slug(slug: str) -> str: ...              # "a/b" -> "a+b"
```
Rules (spec decision 5): max 64 chars; each `/`-segment matches `[a-zA-Z0-9._-]+`; reject `.`/`..` segments (traversal), absolute paths, empty.

**Tests (~4):** `test_validate_slug_rejects_traversal` (`.`/`..`/absolute/over-length/out-of-charset all raise), `test_validate_slug_accepts_valid`, `test_validate_slug_runs_before_filesystem_touch` (bad slug → no dir created), `test_flatten_slug_replaces_slash_with_plus`.

**Acceptance:** criteria 5, 8 + scenario "Path-traversal slug is rejected before touching disk."

---

## PR 3: Worktree lifecycle (outline)

**Branch:** `pr3-worktree` · **Files:** `src/dream/swarm/_worktree.py` (complete), `tests/test_swarm/test_worktree.py`

**Public API** (modelled on OpenHarness `swarm/worktree.py`, lifted + adapted):
```python
@dataclass(frozen=True)
class WorktreeInfo:
    slug: str; path: Path; branch: str; original_path: Path
    created_at: str; agent_id: str | None = None

def create_worktree(repo: Path, slug: str, *, agent_id: str | None = None) -> WorktreeInfo: ...  # idempotent fast-resume
def list_worktrees(repo: Path) -> list[WorktreeInfo]: ...
def remove_worktree(repo: Path, slug: str) -> bool: ...        # unlink symlinks BEFORE git worktree remove
def cleanup_stale(repo: Path, active_agent_ids: set[str] | None) -> list[str]: ...
def _symlink_common_dirs(repo: Path, worktree: Path) -> None:  # node_modules/.venv/__pycache__/.tox; failure non-fatal
```
Uses `subprocess` git: `git worktree add -B worktree-{flat-slug} <path> <base>`; fast-resume when dir exists and `git rev-parse --git-dir` succeeds inside it.

**Tests (~10):** happy_path, fast_resume (no `git worktree add` re-run), `-B` resets orphan branch, symlink non-fatal on failure, remove removes symlinks first (shared originals survive), remove returns False when absent, list recovers slug/branch/origin (`+`→`/`), cleanup_stale prunes only dead agents, cleanup_stale(None) full-sweep, two concurrent worktrees isolated. Use a real temp git repo fixture.

**Acceptance:** criteria 6, 7, 9, 11, 19 + scenarios fast-resume / teardown-symlinks / cleanup_stale. Depends on PR 3 (paths) + PR 4 (slug).

---

## PR 4: Sidecar bundle + `state.json` (outline)

**Branch:** `pr4-sidecar` · **Files:** `src/dream/state/sidecar.py`, `tests/test_state/test_sidecar.py`

**Public API:**
```python
class TaskState(BaseModel):  # pydantic v2
    task_id: str; base_branch: str; created_at: str
    last_checkpoint_turn: int = 0
    status: Literal["running", "paused", "completed", "failed"] = "running"
    parent_checkpoint_ref: str | None = None
    harness_version: str

def create_sidecar(paths: DreamPaths, task_id: str, *, base_branch: str, harness_version: str) -> Path: ...
    # makes sidecars/{task}/{logs,metrics,scratch}, writes state.json atomically (PR1)
def read_state(paths: DreamPaths, task_id: str) -> TaskState: ...
def update_state(paths: DreamPaths, task_id: str, **changes) -> TaskState: ...  # under exclusive_file_lock (PR2)
def remove_sidecar(paths: DreamPaths, task_id: str) -> None: ...
```

**Tests (~5):** create lays out `logs/metrics/scratch/state.json`; state.json round-trips via `TaskState`; `update_state` is lock-guarded + atomic; two tasks have isolated sidecars (no shared file); `harness_version` mismatch raises on read. Uses PR1 fs + PR2 lock + PR3 paths.

**Acceptance:** decision 8, criteria 20, 21 + scenario "Mismatched harness versions refuse to coexist."

---

## PR 5: Checkpoints + resume (outline)

**Branch:** `pr5-checkpoints` · **Files:** `src/dream/state/checkpoints.py`, `tests/test_state/test_checkpoints.py`

**Public API:**
```python
def write_checkpoint(repo: Path, paths: DreamPaths, task_id: str, turn: int) -> str: ...   # commit + ref refs/dream/checkpoints/{t}/{turn}; returns sha
def write_done(repo: Path, paths: DreamPaths, task_id: str) -> str: ...                     # refs/dream/checkpoints/{t}/done
def list_checkpoints(repo: Path, paths: DreamPaths, task_id: str) -> list[tuple[str, str]]: ...  # [(name, sha)]
def resume_from(repo: Path, paths: DreamPaths, source_ref: str, new_task_id: str) -> WorktreeInfo: ...  # new worktree + parent lineage
def gc_checkpoints(repo: Path, paths: DreamPaths, *, older_than_days: int = 30) -> list[str]: ...        # never deletes /done
```
Ref writes via `git update-ref`; checkpoints live under `refs/dream/checkpoints/` (invisible to `git branch`), survive worktree teardown.

**Tests (~6):** ref per turn + done on success; checkpoints survive teardown (worktree gone, refs resolve); resume creates new worktree + records `parent_checkpoint_ref`; resumed tree byte-matches checkpoint commit; task-id never reused; GC respects window + keeps `done`. Uses PR3 + PR5 + git.

**Acceptance:** criteria 10, 15, 16, 17, 18 + scenarios "Checkpoint survives teardown" / "Resume from a checkpoint."

---

## PR 6: Session-start validator + `AGENTS.md` (outline)

**Branch:** `pr6-validator` · **Files:** `src/dream/services/repo_validator.py`, `tests/test_services/test_repo_validator.py`

**Public API:**
```python
@dataclass(frozen=True)
class Finding:
    severity: Literal["blocking", "warning", "info"]; code: str; message: str; path: str | None = None

def validate_repo(paths: DreamPaths) -> list[Finding]: ...
def has_blocking(findings: list[Finding]) -> bool: ...
```
Checks (spec criteria 1–4): `AGENTS.md` present + under hard cap (300) + soft cap warn (100); links resolve; required tree present (`docs/design-docs/core-beliefs.md`, `docs/exec-plans/active/`, `docs/product-specs/`, `docs/references/`, `docs/SECURITY.md`); JSON under `docs/` valid vs declared `$schema`; secret-shaped strings (AWS key/PEM/JWT) blocking + **redacted**; git-ignored required folder treated as missing; stale exec-plan (>7d) warns.

**Tests (~8):** blocks missing/oversized/dead-link/invalid-json/secret; `secret_finding_redacts_value`; `stale_exec_plan_warns_not_blocks`; `git_ignored_required_folder_treated_missing`. Uses PR1 + PR3.

**Acceptance:** criteria 1, 2, 3, 4 + scenarios missing/oversized/secret/stale.

---

## Self-review — spec coverage

Every spec-01 acceptance criterion maps to a PR:

| Criteria | PR | | Criteria | PR |
|---|---|---|---|---|
| 1 (validator blocks) | 6 | | 12 (atomic writes) | 1 |
| 2 (secret redacted) | 6 | | 13 (no temp left / orphan sweep) | 1 |
| 3 (soft warnings) | 6 | | 14 (cross-platform lock) | 1 |
| 4 (git-ignored = missing) | 6 | | 15 (checkpoint per turn / done) | 5 |
| 5 (slug validated first) | 2 | | 16 (checkpoints survive teardown) | 5 |
| 6 (fresh worktree per task) | 3 | | 17 (resume records parent) | 5 |
| 7 (fast-resume) | 3 | | 18 (checkpoint GC) | 5 |
| 8 (flatten slug) | 2 | | 19 (concurrent isolation) | 3 |
| 9 (symlinks removed first) | 3 | | 20 (lock contention loud) | 4 |
| 10 (task-id never reused) | 5 | | 21 (harness_version stamp) | 4 |
| 11 (symlink non-fatal) | 3 | | | |

**Gaps / deferred (with spec sanction):**
- *Hook bus `task.start` emit* (behaviour "Task start" step 4) → `#13`, out of spec-01 scope; the worktree/sidecar primitives are here, the emit is wired later.
- *Promotion of final checkpoint to base branch* (Task-end success) → `#09` policy, explicitly out of scope.
- *`AGENTS.md` initializer / `harness init`* — pranjal-01 criteria 17–19. **Not in the divo-01 consolidation**; tracked as a follow-up PR 7 if you want the `--no-ai` scaffolder. ⏸ deferred (open decision 2).
- *Per-repo validator plug-ins under `.dream/validators/`* (criteria 20–21 of pranjal-01, SHOULD) — deferred to a PR 6.1; core validator ships first. ⏸ deferred (open decision 2).

**Type consistency check:** `DreamPaths` (PR1) is consumed by PR4/5/6 with the same signature; `WorktreeInfo` (PR3) is returned by `resume_from` (PR5); `validate_worktree_slug`/`flatten_slug` (PR2) are used by `create_worktree` (PR3). Names are consistent across tasks.

---

## Execution handoff

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per PR, review between PRs, fast iteration. (REQUIRED SUB-SKILL: superpowers:subagent-driven-development)
2. **Inline Execution** — execute PR-by-PR in this session with checkpoints for your review. (REQUIRED SUB-SKILL: superpowers:executing-plans)

Given your *"smallest change then let me iterate"* instruction, I recommend **Inline**, one PR at a time, stopping after PR 1 for your review before expanding PR 2.
