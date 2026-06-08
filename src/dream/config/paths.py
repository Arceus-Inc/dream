"""The two storage roots (spec 01): of-record (repo) vs per-user (home).

`DreamPaths` is a *pure value object*. Resolving it or reading a path property
never touches the filesystem; directory creation is the explicit, opt-in
`ensure()` call. This diverges deliberately from OpenHarness's side-effecting
`get_*_dir()` accessors (which ``mkdir`` on every read) to keep path computation
referentially transparent and trivially testable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# In-repo now-state directory (git-ignored). Named for the package, not "harness".
DREAM_DIRNAME = ".dream"
# Per-user global root default: ~/.dream
DEFAULT_HOME_DIRNAME = ".dream"
# Env var overriding the per-user global root.
DREAM_HOME_ENV = "DREAM_HOME"
# Checkpoint refs live under this namespace (invisible to `git branch`).
CHECKPOINT_REF_PREFIX = "refs/dream/checkpoints"

__all__ = [
    "CHECKPOINT_REF_PREFIX",
    "DEFAULT_HOME_DIRNAME",
    "DREAM_DIRNAME",
    "DREAM_HOME_ENV",
    "DreamPaths",
]


def _checked_task_id(task_id: str) -> str:
    """Reject task ids that could escape the ``.dream/`` roots (path traversal).

    A last-line guard: the worktree manager (#02) validates slugs up front, but
    these path builders must never join an unsafe segment regardless of caller.

    Scope: the checks are for an ASCII filesystem where ``/`` is the only path
    separator (POSIX) plus ``\\`` for Windows. Unicode separator look-alikes are
    not normalised — they cannot traverse on these filesystems, but a future
    port to an exotic FS should revisit this guard.
    """
    if (
        not task_id
        or task_id in {".", ".."}
        or "/" in task_id
        or "\\" in task_id
        or "\x00" in task_id
        or os.path.isabs(task_id)
    ):
        raise ValueError(f"unsafe task_id: {task_id!r}")
    return task_id


@dataclass(frozen=True)
class DreamPaths:
    """Resolved storage roots and the paths derived from them.

    `repo` is the of-record root (the repository working copy). `home` is the
    per-user global root (default ``~/.dream``). Every other path is computed
    from these two; nothing here creates directories except `ensure()`.
    """

    repo: Path
    home: Path

    @classmethod
    def resolve(
        cls,
        repo: str | os.PathLike[str],
        *,
        home: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> DreamPaths:
        """Resolve roots. `home` precedence: explicit arg > $DREAM_HOME > ~/.dream."""
        env = os.environ if env is None else env
        repo_path = Path(repo).expanduser().resolve()
        if home is not None:
            home_path = Path(home)
        elif DREAM_HOME_ENV in env:
            # Key presence, not truthiness: an explicitly set value is honoured.
            # A present-but-blank value is a misconfiguration we fail loud on
            # rather than silently falling back to ~/.dream.
            raw = env[DREAM_HOME_ENV]
            if not raw.strip():
                raise ValueError(f"{DREAM_HOME_ENV} is set but empty")
            home_path = Path(raw)
        else:
            home_path = Path.home() / DEFAULT_HOME_DIRNAME
        return cls(repo=repo_path, home=home_path.expanduser().resolve())

    # --- repo-side: in-repo now-state (git-ignored) ---

    @property
    def dream_dir(self) -> Path:
        return self.repo / DREAM_DIRNAME

    @property
    def worktrees_dir(self) -> Path:
        return self.dream_dir / "worktrees"

    @property
    def sidecars_dir(self) -> Path:
        return self.dream_dir / "sidecars"

    @property
    def coordination_dir(self) -> Path:
        return self.dream_dir / "coordination"

    @property
    def coordination_board(self) -> Path:
        return self.coordination_dir / "board.sqlite"

    # --- repo-side: of-record (committed) ---

    @property
    def docs_dir(self) -> Path:
        return self.repo / "docs"

    @property
    def exec_plans_active(self) -> Path:
        return self.docs_dir / "exec-plans" / "active"

    @property
    def schemas_dir(self) -> Path:
        return self.docs_dir / "_schemas"

    @property
    def agents_md(self) -> Path:
        return self.repo / "AGENTS.md"

    # --- per-task derived paths ---

    def worktree(self, task_id: str) -> Path:
        return self.worktrees_dir / _checked_task_id(task_id)

    def sidecar(self, task_id: str) -> Path:
        return self.sidecars_dir / _checked_task_id(task_id)

    def trace_log(self, task_id: str) -> Path:
        """The OTel-shaped trace JSONL for a task (Spec 12a)."""
        return self.sidecar(task_id) / "logs" / "trace.jsonl"

    def verification_report(self, task_id: str) -> Path:
        """The verification report JSON for a task (Spec 12c)."""
        return self.sidecar(task_id) / "metrics" / "verification-report.json"

    def tech_debt_matchers(self) -> Path:
        """Operator-declared verification-failure → tech-debt matchers (Spec 12e)."""
        return self.repo / ".harness" / "tech-debt-matchers.toml"

    def sandbox_config(self) -> Path:
        """Operator sandbox posture: tier, extra-allowed roots, credential extras (Spec 13B)."""
        return self.repo / ".harness" / "sandbox.toml"

    def tool_tier_overrides(self) -> Path:
        """Operator trust-ramp promotions for discovered tools/MCPs (Spec 13B)."""
        return self.repo / ".harness" / "tool-tier-overrides.toml"

    def checkpoint_ref(self, task_id: str, n: int | str) -> str:
        return f"{CHECKPOINT_REF_PREFIX}/{_checked_task_id(task_id)}/{n}"

    # --- home-side: per-user global ---

    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"

    @property
    def sessions_dir(self) -> Path:
        return self.home / "data" / "sessions"

    @property
    def tasks_dir(self) -> Path:
        return self.home / "data" / "tasks"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    # --- the one explicit side effect ---

    def ensure(self) -> DreamPaths:
        """Create the in-repo now-state dirs (worktrees/sidecars/coordination).

        Does not create of-record (`docs/`) paths or any repo file. Returns self
        so it chains. Idempotent.
        """
        for directory in (self.worktrees_dir, self.sidecars_dir, self.coordination_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self
