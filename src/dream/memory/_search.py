"""Naive scored search over memory records (spec 11 substrate).

Deliberately simple: case-insensitive term matching scored by where the
term lands (id > description > body). Embedding/semantic retrieval is a
pluggable upgrade behind the same ``MemoryStore.search`` signature.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.contracts.memory import MemoryRecord

__all__ = ["search_records"]

_ID_WEIGHT = 10
_FRONTMATTER_WEIGHT = 5
_BODY_WEIGHT = 1


def search_records(
    records: Sequence[MemoryRecord], query: str, *, limit: int = 20
) -> list[MemoryRecord]:
    """Records matching every query term, best-first, capped at ``limit``."""
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    scored: list[tuple[int, MemoryRecord]] = []
    for record in records:
        score = _score(record, terms)
        if score > 0:
            scored.append((score, record))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [record for _, record in scored[:limit]]


def _score(record: MemoryRecord, terms: list[str]) -> int:
    haystack_id = record.id.lower()
    haystack_front = " ".join(
        str(v).lower() for v in record.frontmatter.values()
    )
    haystack_body = record.content.lower()
    total = 0
    for term in terms:
        if term in haystack_id:
            total += _ID_WEIGHT
        elif term in haystack_front:
            total += _FRONTMATTER_WEIGHT
        elif term in haystack_body:
            total += _BODY_WEIGHT
        else:
            return 0  # every term must match somewhere
    return total
