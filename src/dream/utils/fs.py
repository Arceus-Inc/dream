"""Atomic file-write helpers and shared JSON I/O utilities (spec 01).

Every harness-initiated write goes through here: write to a same-directory temp
file, fsync it, then ``os.replace`` it over the destination. ``os.replace`` is
atomic on POSIX and (since Python 3.3) Windows, so a concurrent reader sees either
the old file or the new one, never a half-written one. A crash before the rename
leaves the destination untouched and an orphan ``{name}.tmp.{uuid}`` that
``clean_orphan_temp_files`` sweeps at task start.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "clean_orphan_temp_files",
    "compact_json",
    "is_json_drop_file",
    "load_json_file",
    "save_json_file",
    "try_load_json_file",
]

_T = TypeVar("_T")

# Temp files are named ``{name}.tmp.{uuid4().hex}`` (32 lowercase hex chars).
# Orphan cleanup matches that exact scheme so it never deletes unrelated files
# that merely happen to contain ``.tmp.``.
_ORPHAN_RE = re.compile(r"\.tmp\.[0-9a-f]{32}\Z")


def atomic_write_bytes(
    path: str | os.PathLike[str], data: bytes, *, mode: int | None = None
) -> None:
    """Write ``data`` to ``path`` atomically (temp -> fsync -> rename)."""
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp.{uuid.uuid4().hex}")
    try:
        # Create the temp file with the requested mode from the start. ``os.open``
        # with O_CREAT|O_EXCL never widens past the given bits (umask can only
        # clear them), so a secret (mode=0o600) never sits in a world-readable
        # file. ``0o666`` for the default path matches the prior ``open()`` mode.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(tmp, flags, mode if mode is not None else 0o666)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
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


def clean_orphan_temp_files(
    directory: str | os.PathLike[str], *, recursive: bool = False
) -> list[Path]:
    """Remove leftover atomic-write temp files; return removed paths.

    Only files matching this writer's exact ``{name}.tmp.{32-hex}`` scheme are
    removed, so unrelated files that merely contain ``.tmp.`` are left untouched.
    With ``recursive=True`` the sweep descends into subdirectories — needed for
    per-task sidecar folders, where ``state.json.tmp.*`` orphans live one level
    down (not in the top-level ``sidecars/`` dir).
    """
    d = Path(directory)
    removed: list[Path] = []
    if not d.is_dir():
        return removed
    candidates = d.rglob("*.tmp.*") if recursive else d.glob("*.tmp.*")
    for p in sorted(candidates):
        if _ORPHAN_RE.search(p.name) is None:
            continue  # not our temp scheme — leave it alone
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


# ---------------------------------------------------------------------------
# JSON I/O helpers — shared across drop-dir queues, artefact files, and
# observability sinks.
# ---------------------------------------------------------------------------


def save_json_file(
    path: str | os.PathLike[str],
    data: dict[str, Any],
    *,
    trailing_newline: bool = True,
    mode: int | None = None,
) -> None:
    """Atomically write *data* as pretty-printed JSON.

    Wraps :func:`atomic_write_text` so callers don't repeat the
    ``json.dumps(…, indent=2) + "\\n"`` boilerplate.
    """
    text = json.dumps(data, indent=2)
    if trailing_newline:
        text += "\n"
    atomic_write_text(path, text, mode=mode)


def load_json_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a UTF-8 JSON file and return the parsed dict.

    Raises the usual ``OSError`` / ``json.JSONDecodeError`` on failure.
    """
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"expected a JSON object in {path}, got {type(data).__name__}"
        )
    return data


def compact_json(payload: dict[str, Any], *, default: Callable[..., Any] | None = str) -> str:
    """Serialize *payload* to a single compact JSON line (no whitespace).

    Used by JSONL sinks and audit-trail writers.  ``default`` is
    forwarded to :func:`json.dumps`; pass ``None`` to disable.
    """
    return json.dumps(payload, separators=(",", ":"), default=default)


def is_json_drop_file(path: Path) -> bool:
    """Return whether *path* looks like a valid drop-dir JSON file.

    Rejects dot-files, non-``.json`` suffixes, atomic-write temp
    artefacts (``*.tmp.*``), and non-regular files.  Shared by the
    command inbox, mailbox, wake-note store, and permission queue.
    """
    name = path.name
    if name.startswith(".") or path.suffix != ".json" or ".tmp." in name:
        return False
    return path.is_file()


def try_load_json_file(
    path: Path,
    constructor: Callable[[dict[str, Any]], _T],
) -> _T | None:
    """Best-effort load: read JSON from *path*, pass the dict to
    *constructor*, and return ``None`` on any expected failure.

    Catches ``OSError``, ``json.JSONDecodeError``, ``KeyError``,
    ``ValueError``, and ``TypeError`` — the set every drop-dir reader
    in the codebase already swallowed individually.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return constructor(data)
    except (KeyError, ValueError, TypeError):
        return None
