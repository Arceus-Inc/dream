"""ShadowCheckpointManager — ensure / list / restore (Hermes CheckpointManager)."""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Sequence
from pathlib import Path

from dream.engine._messages import ConversationMessage
from dream.state.shadow._rewind import rewind_transcript
from dream.state.shadow._store import ShadowCheckpointStore
from dream.state.shadow._types import (
    CheckpointOutcome,
    CheckpointReason,
    CheckpointSnapshot,
    CombinedRestoreResult,
    EnsureResult,
    RestoreOutcome,
    RestoreResult,
    ShadowCheckpointConfig,
)

# Safety snap outcomes that still allow restore to proceed.
_SAFETY_OK = frozenset(
    {
        CheckpointOutcome.TAKEN,
        CheckpointOutcome.NO_CHANGES,
    }
)


class ShadowCheckpointManager:
    """Automatic pre-mutate filesystem snapshots via a shared shadow store."""

    def __init__(
        self,
        *,
        store: ShadowCheckpointStore,
        config: ShadowCheckpointConfig | None = None,
    ) -> None:
        self._store = store
        self._config = config or ShadowCheckpointConfig()
        self._checkpointed_dirs: dict[str | None, set[Path]] = {}
        self._git_available: bool | None = None

    @property
    def config(self) -> ShadowCheckpointConfig:
        return self._config

    def begin_turn(self, session_id: str | None = None) -> None:
        """Reset per-turn dedup (call on each USER_PROMPT_SUBMIT / agent turn)."""
        self._checkpointed_dirs.pop(session_id, None)

    def ensure(
        self,
        working_dir: Path,
        *,
        reason: CheckpointReason,
        session_id: str | None = None,
    ) -> EnsureResult:
        """Take a checkpoint if enabled and not already done this turn."""
        if not self._config.enabled:
            return EnsureResult(outcome=CheckpointOutcome.DISABLED)

        if self._git_available is None:
            self._git_available = shutil.which("git") is not None
        if not self._git_available:
            return EnsureResult(outcome=CheckpointOutcome.GIT_UNAVAILABLE)

        try:
            abs_dir = working_dir.resolve()
        except OSError as exc:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=str(exc))

        if abs_dir == Path("/").resolve() or abs_dir == Path.home().resolve():
            return EnsureResult(outcome=CheckpointOutcome.DIRECTORY_TOO_BROAD)

        checkpointed_dirs = self._checkpointed_dirs.setdefault(session_id, set())
        if abs_dir in checkpointed_dirs:
            return EnsureResult(outcome=CheckpointOutcome.ALREADY_THIS_TURN)

        try:
            result = self._take(abs_dir, reason)
        except Exception as exc:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=str(exc))

        # Only suppress retries after a conclusive snap (or no-op). Transient
        # FAILED must leave the turn open so a later mutate can try again.
        if result.outcome is not CheckpointOutcome.FAILED:
            checkpointed_dirs.add(abs_dir)
        return result

    def list_for(self, working_dir: Path) -> list[CheckpointSnapshot]:
        """List retained checkpoints for ``working_dir`` (newest first)."""
        abs_dir = working_dir.resolve()
        store = self._store.store_path
        if not (store / "HEAD").exists():
            return []
        ref = self._store.ref_name(abs_dir)
        rc, stdout, _err = self._store.git(
            ["log", ref, "--format=%H|%h|%s", "-n", str(self._config.max_snapshots)],
            working_dir=abs_dir,
            index=False,
            timeout=self._config.timeout_seconds,
        )
        if rc != 0 or not stdout:
            return []
        out: list[CheckpointSnapshot] = []
        for line in stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            sha, short, subject = parts
            try:
                reason: CheckpointReason | str = CheckpointReason(subject)
            except ValueError:
                reason = subject
            out.append(
                CheckpointSnapshot(
                    commit_sha=sha,
                    short_sha=short,
                    reason=reason,
                    working_dir=abs_dir,
                )
            )
        return out

    def restore(self, working_dir: Path, *, commit_sha: str) -> RestoreResult:
        """Restore the working tree to ``commit_sha`` (after a safety snap)."""
        if not self._config.enabled:
            return RestoreResult(outcome=RestoreOutcome.DISABLED)
        abs_dir = working_dir.resolve()
        if not (self._store.store_path / "HEAD").exists():
            return RestoreResult(outcome=RestoreOutcome.NOT_FOUND, detail="empty store")

        rc, kind, err = self._store.git(
            ["cat-file", "-t", commit_sha],
            working_dir=abs_dir,
            index=False,
            timeout=self._config.timeout_seconds,
        )
        if rc != 0 or kind != "commit":
            return RestoreResult(
                outcome=RestoreOutcome.NOT_FOUND,
                detail=err or f"unknown commit {commit_sha}",
            )

        # Safety snap before rollback — refuse to overwrite current state without it.
        self.begin_turn()
        safety = self.ensure(abs_dir, reason=CheckpointReason.PRE_ROLLBACK)
        if safety.outcome not in _SAFETY_OK:
            return RestoreResult(
                outcome=RestoreOutcome.FAILED,
                detail=(safety.detail or f"pre-rollback snapshot failed ({safety.outcome.value})"),
            )

        self._store.index_path(abs_dir).parent.mkdir(parents=True, exist_ok=True)
        timeout = self._config.timeout_seconds * 2
        # Reset index + worktree to the checkpoint tree (drops post-snap paths).
        rc, _out, err = self._store.git(
            ["read-tree", "-u", "--reset", commit_sha],
            working_dir=abs_dir,
            index=True,
            timeout=timeout,
        )
        if rc != 0:
            return RestoreResult(outcome=RestoreOutcome.FAILED, detail=err)

        return RestoreResult(outcome=RestoreOutcome.RESTORED, restored_to=commit_sha[:8])

    def restore_and_rewind(
        self,
        working_dir: Path,
        *,
        commit_sha: str,
        messages: Sequence[ConversationMessage],
        rewind_turns: int = 1,
    ) -> CombinedRestoreResult:
        """Restore the worktree and truncate the conversation (Hermes ``/rollback``).

        Transcript rewind runs only after a successful filesystem restore so a
        failed snap never desyncs chat from disk. ``rewind_turns=0`` keeps the
        transcript unchanged (FS-only restore).
        """
        if rewind_turns < 0:
            return CombinedRestoreResult(
                fs=RestoreResult(
                    outcome=RestoreOutcome.FAILED,
                    detail=f"turns must be >= 0; got {rewind_turns}",
                ),
                messages=tuple(messages),
                transcript_removed=0,
            )
        fs = self.restore(working_dir, commit_sha=commit_sha)
        if fs.outcome is not RestoreOutcome.RESTORED:
            return CombinedRestoreResult(fs=fs, messages=tuple(messages), transcript_removed=0)
        kept, removed = rewind_transcript(messages, turns=rewind_turns)
        return CombinedRestoreResult(
            fs=fs,
            messages=tuple(kept),
            transcript_removed=removed,
        )

    def _take(self, working_dir: Path, reason: CheckpointReason) -> EnsureResult:
        err = self._store.ensure_initialized(working_dir)
        if err:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=err)

        index = self._store.index_path(working_dir)
        index.parent.mkdir(parents=True, exist_ok=True)
        ref = self._store.ref_name(working_dir)
        timeout = self._config.timeout_seconds

        rc_ref, tip, _ = self._store.git(
            ["rev-parse", "--verify", f"{ref}^{{commit}}"],
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        has_ref = rc_ref == 0 and bool(tip)
        if has_ref:
            self._store.git(
                ["read-tree", tip],
                working_dir=working_dir,
                index=True,
                timeout=timeout,
            )
        elif index.exists():
            with contextlib.suppress(OSError):
                index.unlink()

        rc_add, _out, err_add = self._store.git(
            ["add", "-A"],
            working_dir=working_dir,
            index=True,
            timeout=timeout * 2,
        )
        if rc_add != 0:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=err_add)

        if has_ref:
            rc_diff, _, _ = self._store.git(
                ["diff-index", "--cached", "--quiet", tip],
                working_dir=working_dir,
                index=True,
                timeout=timeout,
            )
            if rc_diff == 0:
                return EnsureResult(outcome=CheckpointOutcome.NO_CHANGES)
        else:
            rc_ls, ls_out, _ = self._store.git(
                ["ls-files", "--cached"],
                working_dir=working_dir,
                index=True,
                timeout=timeout,
            )
            if rc_ls == 0 and not ls_out.strip():
                return EnsureResult(outcome=CheckpointOutcome.NO_CHANGES)

        rc_tree, tree_sha, err_tree = self._store.git(
            ["write-tree"],
            working_dir=working_dir,
            index=True,
            timeout=timeout,
        )
        if rc_tree != 0 or not tree_sha:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=err_tree)

        if has_ref:
            commit_args = [
                "commit-tree",
                tree_sha,
                "-p",
                tip,
                "-m",
                reason.value,
                "--no-gpg-sign",
            ]
        else:
            commit_args = ["commit-tree", tree_sha, "-m", reason.value, "--no-gpg-sign"]
        rc_commit, new_sha, err_commit = self._store.git(
            commit_args,
            working_dir=working_dir,
            index=True,
            timeout=timeout,
        )
        if rc_commit != 0 or not new_sha:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=err_commit)

        update_args = ["update-ref", ref, new_sha, tip] if has_ref else ["update-ref", ref, new_sha]
        rc_up, _, err_up = self._store.git(
            update_args,
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        if rc_up != 0:
            return EnsureResult(outcome=CheckpointOutcome.FAILED, detail=err_up)

        self._prune(working_dir, ref)

        # Tip SHA may change when prune rewrites history — always read the live ref.
        rc_tip, tip_sha, _ = self._store.git(
            ["rev-parse", "--verify", f"{ref}^{{commit}}"],
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        live = tip_sha if rc_tip == 0 and tip_sha else new_sha
        snap = CheckpointSnapshot(
            commit_sha=live,
            short_sha=live[:8],
            reason=reason,
            working_dir=working_dir,
        )
        return EnsureResult(outcome=CheckpointOutcome.TAKEN, snapshot=snap)

    def _prune(self, working_dir: Path, ref: str) -> None:
        """Rewrite ``ref`` to retain at most ``max_snapshots`` commits (Hermes v2)."""
        max_n = self._config.max_snapshots
        if max_n < 1:
            return
        timeout = self._config.timeout_seconds
        rc_count, count_out, _ = self._store.git(
            ["rev-list", "--count", ref],
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        if rc_count != 0:
            return
        try:
            count = int(count_out)
        except ValueError:
            return
        if count <= max_n:
            return

        rc_list, list_out, _ = self._store.git(
            ["rev-list", "--reverse", ref],
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        if rc_list != 0 or not list_out:
            return
        keep = list_out.splitlines()[-max_n:]

        new_parent: str | None = None
        for sha in keep:
            rc_tree, tree_sha, _ = self._store.git(
                ["rev-parse", f"{sha}^{{tree}}"],
                working_dir=working_dir,
                index=False,
                timeout=timeout,
            )
            if rc_tree != 0 or not tree_sha:
                return
            rc_msg, msg, _ = self._store.git(
                ["log", "--format=%s", "-1", sha],
                working_dir=working_dir,
                index=False,
                timeout=timeout,
            )
            commit_msg = msg if rc_msg == 0 and msg else "checkpoint"
            if new_parent is None:
                args = ["commit-tree", tree_sha, "-m", commit_msg, "--no-gpg-sign"]
            else:
                args = [
                    "commit-tree",
                    tree_sha,
                    "-p",
                    new_parent,
                    "-m",
                    commit_msg,
                    "--no-gpg-sign",
                ]
            rc_commit, new_sha, _ = self._store.git(
                args,
                working_dir=working_dir,
                index=False,
                timeout=timeout,
            )
            if rc_commit != 0 or not new_sha:
                return
            new_parent = new_sha

        if new_parent is None:
            return
        self._store.git(
            ["update-ref", ref, new_parent],
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        self._store.git(
            ["reflog", "expire", "--expire=now", "--all"],
            working_dir=working_dir,
            index=False,
            timeout=timeout,
        )
        self._store.git(
            ["gc", "--prune=now", "--quiet"],
            working_dir=working_dir,
            index=False,
            timeout=timeout * 3,
        )


__all__ = ["ShadowCheckpointManager"]
