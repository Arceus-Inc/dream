"""Build and apply filesystem commits with rollback."""

from __future__ import annotations

from dream.tools.apply_patch._io import PatchFileOps
from dream.tools.apply_patch._types import (
    ActionType,
    Commit,
    DiffError,
    FileChange,
    Patch,
    PatchAction,
)


def patch_to_commit(patch: Patch, file_contents: dict[str, str]) -> Commit:
    commit = Commit()
    for path, action in patch.actions.items():
        if action.type == ActionType.DELETE:
            commit.changes[path] = FileChange(
                type=ActionType.DELETE,
                old_content=file_contents[path],
            )
        elif action.type == ActionType.ADD:
            commit.changes[path] = FileChange(
                type=ActionType.ADD,
                new_content=action.new_file,
            )
        elif action.type == ActionType.UPDATE:
            commit.changes[path] = FileChange(
                type=ActionType.UPDATE,
                old_content=file_contents[path],
                new_content=_merge_hunks(file_contents[path], action, path),
                move_path=action.move_path,
            )
    return commit


def apply_commit(commit: Commit, ops: PatchFileOps) -> None:
    applied: list[tuple[str, FileChange]] = []
    try:
        for path, change in commit.changes.items():
            _apply_change(path, change, ops)
            applied.append((path, change))
    except Exception:
        _rollback(applied, ops)
        raise


def _merge_hunks(text: str, action: PatchAction, path: str) -> str:
    if action.type != ActionType.UPDATE:
        raise DiffError(f"expected update action for {path}")
    source_lines = text.split("\n")
    output: list[str] = []
    cursor = 0
    for chunk in action.chunks:
        if chunk.orig_index > len(source_lines):
            raise DiffError(
                f"update {path}: chunk index {chunk.orig_index} > line count {len(source_lines)}"
            )
        if cursor > chunk.orig_index:
            raise DiffError(
                f"update {path}: cursor {cursor} > chunk index {chunk.orig_index}"
            )
        output.extend(source_lines[cursor : chunk.orig_index])
        cursor = chunk.orig_index
        output.extend(chunk.ins_lines)
        cursor += len(chunk.del_lines)
    output.extend(source_lines[cursor:])
    return "\n".join(output)


def _apply_change(path: str, change: FileChange, ops: PatchFileOps) -> None:
    if change.type == ActionType.DELETE:
        ops.delete(path)
        return
    if change.type == ActionType.ADD:
        if change.new_content is None:
            raise DiffError(f"Add File missing content: {path}")
        ops.write(path, change.new_content)
        return
    if change.new_content is None:
        raise DiffError(f"Update File missing content: {path}")
    if change.move_path:
        ops.write(change.move_path, change.new_content)
        try:
            ops.delete(path)
        except Exception:
            ops.delete(change.move_path)
            raise
        return
    ops.write(path, change.new_content)


def _rollback(applied: list[tuple[str, FileChange]], ops: PatchFileOps) -> None:
    for path, change in reversed(applied):
        if change.type == ActionType.ADD:
            ops.delete(path)
        elif change.type == ActionType.DELETE and change.old_content is not None:
            ops.write(path, change.old_content)
        elif change.type == ActionType.UPDATE:
            if change.move_path:
                ops.delete(change.move_path)
                if change.old_content is not None:
                    ops.write(path, change.old_content)
            elif change.old_content is not None:
                ops.write(path, change.old_content)


__all__ = ["apply_commit", "patch_to_commit"]
