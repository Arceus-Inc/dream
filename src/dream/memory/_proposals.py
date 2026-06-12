"""Task memory — the outbound proposal seam (spec 11a; spec 11 decision #2).

A task may *nominate* a durable fact for promotion but it may not promote it —
that decision belongs to lattice's slower clock, made with evidence from many
tasks. dream's only write path toward durable memory is this append-only queue:
:func:`write_proposal` drops a ``{ts}-{slug}.md`` file into ``_proposals/`` and
returns. dream never reads, scores, or resolves what it wrote.

The queue lives under the dream **home** (next to the durable store dream
*reads*), not under the worktree, so a proposal **survives** the worktree
teardown that kills working memory.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from dream.memory._paths import project_memory_dir
from dream.utils.fs import atomic_write_text

__all__ = [
    "InvalidSlugError",
    "proposals_dir",
    "validate_slug",
    "write_proposal",
]

# A slug is a filesystem- and URL-safe handle: lowercase alphanumerics and
# single hyphens, no leading/trailing hyphen. This rejects path separators,
# traversal (``..``), spaces, and uppercase before any path is built from it.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PROPOSALS_DIRNAME = "_proposals"


class InvalidSlugError(ValueError):
    """A proposal slug failed validation."""


def validate_slug(slug: str) -> str:
    """Return ``slug`` unchanged if valid; raise :class:`InvalidSlugError` if not.

    Validation runs *before* any path is constructed, so a hostile slug
    (``../escape``, ``a/b``, ``""``) can never escape the proposals directory.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise InvalidSlugError(
            f"invalid slug {slug!r}: use lowercase letters, digits, and hyphens "
            "(no path separators, spaces, or leading/trailing hyphen)"
        )
    return slug


def proposals_dir(home: Path, repo: Path) -> Path:
    """The durable proposals queue for one project under one dream home."""
    return project_memory_dir(home, repo) / _PROPOSALS_DIRNAME


def write_proposal(
    directory: Path,
    *,
    slug: str,
    content: str,
    rationale: str,
    source: str,
) -> Path:
    """Write a ``{ts}-{slug}.md`` proposal into ``directory`` and return its path.

    Raises :class:`InvalidSlugError` if ``slug`` is malformed (no file is
    written). The body is the candidate durable-memory content; the frontmatter
    records who proposed it and why so lattice's dream phase can score it.
    """
    validate_slug(slug)
    created = datetime.now(tz=UTC)
    ts = created.strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{ts}-{slug}.md"
    document = (
        "---\n"
        f"slug: {slug}\n"
        f"source: {source}\n"
        f"created: {created.isoformat()}\n"
        f"rationale: {_yaml_scalar(rationale)}\n"
        "---\n\n"
        f"{content.rstrip()}\n"
    )
    atomic_write_text(target, document)
    return target


def _yaml_scalar(value: str) -> str:
    """Render ``value`` as a safe single-line YAML scalar.

    Rationale is free text; double-quote it and escape quotes/backslashes so a
    colon or ``#`` never breaks the frontmatter. Newlines collapse to spaces —
    the rationale is a one-liner by contract.
    """
    flattened = " ".join(value.split())
    escaped = flattened.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
