"""Public factory: build the session Tracer (JSONL + OTel by default).

OTel is always composed in unless ``OTEL_SDK_DISABLED=true``. The OTLP endpoint
defaults to ``http://localhost:4318`` when unset.
"""

from __future__ import annotations

import logging

from dream.observability._composite import CompositeTracer
from dream.observability._otel_config import is_otel_enabled, load_otel_config
from dream.observability._otel_provider import get_otel_tracer
from dream.observability._otel_tracer import OtelTracer
from dream.observability._tracer import JsonlTracer, NoopTracer, Tracer
from dream.observability._writer import TraceWriter

_logger = logging.getLogger("dream.observability.otel")


def build_session_tracer(
    *,
    session_id: str,
    task_id: str | None,
    writer: TraceWriter | None,
) -> Tracer:
    """Return JSONL tracer fanned out to OTel (unless SDK disabled)."""
    jsonl: Tracer
    if writer is None:
        jsonl = NoopTracer()
    else:
        jsonl = JsonlTracer(writer, session_id=session_id, task_id=task_id)

    if not is_otel_enabled():
        return jsonl

    handle = get_otel_tracer(config=load_otel_config())
    if handle is None:
        _logger.warning("OTel provider failed to start; continuing with JSONL-only tracing.")
        return jsonl
    otel = OtelTracer(handle.tracer, session_id=session_id, task_id=task_id)
    return CompositeTracer((jsonl, otel))


__all__ = ["build_session_tracer"]
