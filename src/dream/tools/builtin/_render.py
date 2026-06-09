"""Shared field-rendering helper for human-readable tool ``content`` blocks.

Several read tools (``task_get``, ``cron_show``) render a record as a block of
``label: value`` lines where optional fields are emitted only when present.
This collapses that repeated "append a line per non-empty field" loop.
"""

from __future__ import annotations

from collections.abc import Iterable


def render_fields(fields: Iterable[tuple[str, object | None]]) -> str:
    """Join ``label: value`` lines, skipping fields whose value is ``None``.

    Each field is a ``(label, value)`` pair. Values are stringified as-is, so
    callers pre-format anything that needs it (e.g. ``dt.isoformat()``); a
    ``None`` value drops the line entirely.
    """
    return "\n".join(f"{label}: {value}" for label, value in fields if value is not None)


__all__ = ["render_fields"]
