"""TracerProvider setup — lazy, opt-in OTLP export (Spec 12 exporter path).

Imported only after :func:`dream.observability._otel_config.is_otel_enabled`
returns True (or from tests injecting an in-memory exporter). Uses
``BatchSpanProcessor`` so ``span.end()`` is a non-blocking enqueue (hermes-otel).
"""

from __future__ import annotations

import atexit
import logging
from contextlib import suppress
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Tracer as SdkTracer

from dream.observability._otel_config import OtelConfig

_logger = logging.getLogger("dream.observability.otel")

_provider: TracerProvider | None = None
_handle: OtelProviderHandle | None = None


@dataclass(frozen=True)
class OtelProviderHandle:
    """Live provider + tracer for one process."""

    enabled: bool
    tracer: SdkTracer
    provider: TracerProvider

    def force_flush(self, timeout_millis: int = 5_000) -> bool:
        return bool(self.provider.force_flush(timeout_millis))

    def shutdown(self) -> None:
        self.provider.shutdown()


def build_tracer_provider(
    config: OtelConfig,
    *,
    span_exporter: SpanExporter | None = None,
    service_name: str | None = None,
    service_version: str | None = None,
) -> OtelProviderHandle:
    """Build (or return cached) TracerProvider.

    When ``span_exporter`` is provided (tests), it replaces the OTLP exporter.
    When ``config.enabled`` is False and no exporter is injected, returns a
    no-op handle backed by a never-exported provider (still typed).
    """
    global _provider, _handle
    if _handle is not None and span_exporter is None:
        return _handle

    name = service_name or config.service_name
    version = service_version or config.service_version
    resource = Resource.create(
        {
            "service.name": name,
            "service.version": version,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = span_exporter
    if exporter is None and config.enabled and config.endpoint is not None:
        exporter = _build_otlp_exporter(config.endpoint)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    # Only install globally when we own process-wide telemetry.
    if span_exporter is None:
        trace.set_tracer_provider(provider)
        _provider = provider
        atexit.register(_shutdown_provider)
    tracer = provider.get_tracer("dream.observability", version)
    handle = OtelProviderHandle(enabled=True, tracer=tracer, provider=provider)
    if span_exporter is None:
        _handle = handle
    return handle


def get_otel_tracer(
    *,
    config: OtelConfig | None = None,
    span_exporter: SpanExporter | None = None,
) -> OtelProviderHandle | None:
    """Return a provider handle when enabled (or when a test exporter is passed)."""
    from dream.observability._otel_config import load_otel_config

    resolved = config if config is not None else load_otel_config()
    if not resolved.enabled and span_exporter is None:
        return None
    try:
        return build_tracer_provider(resolved, span_exporter=span_exporter)
    except ImportError:
        _logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry packages are "
            "missing; install dream[otel]. Running without OTLP export.",
        )
        return None


def reset_otel_provider_for_tests() -> None:
    """Drop the process-global provider (tests only)."""
    global _provider, _handle
    if _handle is not None:
        with suppress(Exception):
            _handle.shutdown()
    _provider = None
    _handle = None


def _build_otlp_exporter(endpoint: str) -> SpanExporter:
    # Prefer HTTP exporter (Langfuse / Tempo / hermes-otel default).
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=_traces_url(endpoint))


def _traces_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


def _shutdown_provider() -> None:
    global _provider, _handle
    if _handle is not None:
        try:
            _handle.shutdown()
        except Exception:
            _logger.debug("otel provider shutdown failed", exc_info=True)
    _provider = None
    _handle = None


__all__ = [
    "OtelProviderHandle",
    "build_tracer_provider",
    "get_otel_tracer",
    "reset_otel_provider_for_tests",
]
