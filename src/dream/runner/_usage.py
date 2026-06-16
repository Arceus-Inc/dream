"""Per-model token metering for the task runner.

``UsageMeter`` wraps an optional inner ``RunTaskObserver``, folds
``role.session.closed`` events into a ``dict[str, UsageSnapshot]``
summed per model, and forwards every event to the inner observer so
nothing the user observer expects is lost.

``usage_from_event`` is a pure helper: it parses a single
``role.session.closed`` event dict and returns ``(model, UsageSnapshot)``
or ``None`` when the event is not meterable (wrong kind, missing/empty
model, non-mapping usage).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.runner._observer import RunTaskObserver

__all__ = ["UsageMeter", "usage_from_event"]

_CLOSE_KIND = "role.session.closed"


def usage_from_event(event: Mapping[str, Any]) -> tuple[str, UsageSnapshot] | None:
    """(model, usage) for a meterable role.session.closed event, else None."""
    if event.get("kind") != _CLOSE_KIND:
        return None
    model = event.get("model")
    usage = event.get("usage")
    if not isinstance(model, str) or not model or not isinstance(usage, Mapping):
        return None
    return model, UsageSnapshot(
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cache_read_tokens=int(usage.get("cache_read_tokens", 0)),
        cache_write_tokens=int(usage.get("cache_write_tokens", 0)),
    )


class UsageMeter:
    """Wraps an optional observer; accumulates per-model token usage."""

    def __init__(self, inner: RunTaskObserver | None = None) -> None:
        self._inner = inner
        self._by_model: dict[str, UsageSnapshot] = {}

    def on_event(self, event: dict[str, Any]) -> None:
        metered = usage_from_event(event)
        if metered is not None:
            model, usage = metered
            self._by_model[model] = self._by_model.get(model, UsageSnapshot()) + usage
        if self._inner is not None:
            self._inner.on_event(event)

    @property
    def usage_by_model(self) -> Mapping[str, UsageSnapshot]:
        return dict(self._by_model)
