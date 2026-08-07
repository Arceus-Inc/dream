"""Public factory: build the session Tracer (JSONL + OTel by default).

OTel is always composed in unless ``OTEL_SDK_DISABLED=true``. The OTLP endpoint
defaults to ``http://localhost:4318`` when unset.
"""

from __future__ import annotations

import warnings

from dream.observability._composite import CompositeTracer
from dream.observability._otel_config import is_otel_enabled, load_otel_config
from dream.observability._otel_provider import get_otel_tracer
from dream.observability._otel_tracer import OtelTracer
from dream.observability._tracer import JsonlTracer, NoopTracer, Tracer
from dream.observability._writer import TraceWriter

_missing_otel_warning_emitted = False


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
        global _missing_otel_warning_emitted
        if not _missing_otel_warning_emitted:
            warnings.warn(
                "OTel provider failed to start; continuing with JSONL-only tracing.",
                RuntimeWarning,
                stacklevel=2,
            )
            _missing_otel_warning_emitted = True
        return jsonl
    otel = OtelTracer(handle.tracer, session_id=session_id, task_id=task_id)
    return CompositeTracer((jsonl, otel))


__all__ = ["build_session_tracer"]
