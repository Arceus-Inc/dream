"""Shared bare shadow-git store (Hermes v2 layout).

Layout under ``base_dir``::

    store/                 # bare git repo (GIT_DIR)
    store/indexes/<hash>   # per-project index files
    store/projects/<hash>  # touch markers

Uses ``GIT_DIR`` + ``GIT_WORK_TREE`` + ``GIT_INDEX_FILE`` so no ``.git``
leaks into the agent's working tree.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from dream.utils.fs import atomic_write_text
from dream.utils.git import run_git

_STORE_DIRNAME = "store"
_INDEXES_DIRNAME = "indexes"
_PROJECTS_DIRNAME = "projects"
_REF_PREFIX = "refs/dream/shadow"


@dataclass(frozen=True, slots=True)
class ShadowCheckpointStore:
    """Filesystem location of the shared shadow store."""

    base_dir: Path

    @property
    def store_path(self) -> Path:
        return self.base_dir / _STORE_DIRNAME

    def project_hash(self, working_dir: Path) -> str:
        normalized = str(working_dir.resolve())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def ref_name(self, working_dir: Path) -> str:
        return f"{_REF_PREFIX}/{self.project_hash(working_dir)}"

    def index_path(self, working_dir: Path) -> Path:
        return self.store_path / _INDEXES_DIRNAME / self.project_hash(working_dir)

    def shadow_env(self, working_dir: Path, *, index: bool = True) -> dict[str, str]:
        """Build an isolated git env pointing at this store + work tree."""
        env = {**os.environ}
        env["GIT_DIR"] = str(self.store_path)
        env["GIT_WORK_TREE"] = str(working_dir.resolve())
        env.pop("GIT_NAMESPACE", None)
        env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
        if index:
            env["GIT_INDEX_FILE"] = str(self.index_path(working_dir))
        else:
            env.pop("GIT_INDEX_FILE", None)
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_AUTHOR_NAME"] = "Dream Checkpoint"
        env["GIT_AUTHOR_EMAIL"] = "checkpoint@dream.local"
        env["GIT_COMMITTER_NAME"] = "Dream Checkpoint"
        env["GIT_COMMITTER_EMAIL"] = "checkpoint@dream.local"
        return env

    def ensure_initialized(self, working_dir: Path) -> str | None:
        """Create bare store if needed. Returns error detail or ``None``."""
        store = self.store_path
        try:
            store.mkdir(parents=True, exist_ok=True)
            (store / _INDEXES_DIRNAME).mkdir(parents=True, exist_ok=True)
            (store / _PROJECTS_DIRNAME).mkdir(parents=True, exist_ok=True)
            for subdir in ("refs/heads", "branches", "refs/dream/shadow"):
                (store / subdir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"cannot create store: {exc}"

        if not (store / "HEAD").exists():
            # init in store itself without work-tree overlay
            rc, _out, err = run_git(["init", "--bare"], cwd=store)
            if rc != 0:
                return f"git init --bare failed: {err}"
            run_git(["config", "user.name", "Dream Checkpoint"], cwd=store)
            run_git(["config", "user.email", "checkpoint@dream.local"], cwd=store)

        # touch project marker
        marker = store / _PROJECTS_DIRNAME / f"{self.project_hash(working_dir)}.path"
        with contextlib.suppress(OSError):
            atomic_write_text(marker, str(working_dir.resolve()) + "\n", encoding="utf-8")
        return None

    def git(
        self,
        args: list[str],
        *,
        working_dir: Path,
        index: bool = True,
        timeout: float | None = 30.0,
    ) -> tuple[int, str, str]:
        env = self.shadow_env(working_dir, index=index)
        return run_git(args, cwd=working_dir, env=env, timeout=timeout)


__all__ = ["ShadowCheckpointStore"]
