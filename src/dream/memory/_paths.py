"""Per-project memory directory resolution (spec 11 / spec 01 storage layout).

``~/.dream/memory/{project}-{sha}/`` — the slug keeps directories
human-greppable, the hash keeps two checkouts named ``app`` apart.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["project_memory_dir"]

_HASH_CHARS = 8


def project_memory_dir(home: Path, repo: Path) -> Path:
    """The durable memory root for one project under one dream home."""
    resolved = Path(repo).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:_HASH_CHARS]
    return Path(home) / "memory" / f"{resolved.name}-{digest}"
