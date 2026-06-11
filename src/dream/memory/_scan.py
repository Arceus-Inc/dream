"""Scan a memory directory into ``MemoryRecord``s (spec 11 substrate).

One record per ``*.md`` file with YAML frontmatter (``name`` /
``description`` / ``metadata.type`` / ``metadata.scope``). ``MEMORY.md``
is the human index, never a record. A corrupt file is skipped — memory
is advisory context; a bad record must not take the session down.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from dream.contracts.memory import MemoryRecord, MemoryScope, MemoryType

__all__ = ["scan_memory_dir"]

_INDEX_FILENAME = "MEMORY.md"
_FENCE = "---"


def scan_memory_dir(root: Path) -> tuple[MemoryRecord, ...]:
    """Parse every well-formed record under ``root``, sorted by id."""
    if not root.is_dir():
        return ()
    records: list[MemoryRecord] = []
    for path in sorted(root.glob("*.md")):
        if path.name == _INDEX_FILENAME:
            continue
        record = _try_parse(path)
        if record is not None:
            records.append(record)
    return tuple(records)


def _try_parse(path: Path) -> MemoryRecord | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = _split_frontmatter(text)
    if parts is None:
        return None
    header, body = parts
    try:
        loaded = yaml.safe_load(header)
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    metadata = loaded.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    record_id = str(loaded.get("name") or path.stem)
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        modified_at = None
    return MemoryRecord(
        id=record_id,
        scope=_scope(metadata.get("scope")),
        type=_type(metadata.get("type")),
        content=body.strip(),
        source=path,
        modified_at=modified_at,
        frontmatter=_flatten_frontmatter(loaded),
    )


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if end is None:
        return None
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1 :])


def _scope(value: object) -> MemoryScope:
    try:
        return MemoryScope(str(value))
    except ValueError:
        return MemoryScope.PROJECT


def _type(value: object) -> MemoryType:
    try:
        return MemoryType(str(value))
    except ValueError:
        return MemoryType.REFERENCE


def _flatten_frontmatter(loaded: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in loaded.items() if k != "metadata"} | dict(
        loaded.get("metadata") or {}
    )
