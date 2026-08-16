"""TracerProvider setup — lazy, default-on OTLP export beside JSONL.

Imported by the session tracer factory when OTel is enabled. Uses
``BatchSpanProcessor`` so ``span.end()`` is a non-blocking enqueue.

Collector-absent shutdown is bounded: process exit waits at most
:data:`SHUTDOWN_TIMEOUT_SECONDS` for the provider to flush. A missing
collector does **not** turn OTLP off (JSONL stays the durable substrate;
OTLP remains the additive sink). Set ``OTEL_SDK_DISABLED=true`` when no
collector exists and you do not want export attempts.
"""

from __future__ import annotations

import atexit
import threading
import warnings
from contextlib import suppress
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Tracer as SdkTracer

from dream.observability._otel_config import OtelConfig

# Process-wide provider (OTel's process model). Not harness config state.
_provider: TracerProvider | None = None
_handle: OtelProviderHandle | None = None

# Python BatchSpanProcessor accepts export_timeout_millis but does not enforce
# it; the OTLP HTTP exporter timeout is the effective per-attempt bound.
# atexit still joins shutdown on a worker so a hung exporter cannot stall exit.
SHUTDOWN_TIMEOUT_SECONDS = 5.0
EXPORTER_TIMEOUT_SECONDS = 2.0
_FLUSH_TIMEOUT_MILLIS = 5_000


@dataclass(frozen=True)
class OtelProviderHandle:
    """Live provider + tracer for one process."""

    enabled: bool
    tracer: SdkTracer
    provider: TracerProvider

    def force_flush(self, timeout_millis: int = _FLUSH_TIMEOUT_MILLIS) -> bool:
        return bool(self.provider.force_flush(timeout_millis))

    def shutdown(self, timeout_seconds: float | None = None) -> None:
        """Flush and shut down, waiting at most ``timeout_seconds``.

        Default is :data:`SHUTDOWN_TIMEOUT_SECONDS`. A missing collector must
        not hang process exit; spans that cannot export within the bound are
        dropped. JSONL is unaffected.
        """
        bound = SHUTDOWN_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        _shutdown_provider_bounded(self.provider, timeout_seconds=bound)


def build_tracer_provider(
    config: OtelConfig,
    *,
    span_exporter: SpanExporter | None = None,
    service_name: str | None = None,
    service_version: str | None = None,
) -> OtelProviderHandle:
    """Build (or return cached) TracerProvider.

    When ``span_exporter`` is provided (tests), it replaces the OTLP exporter
    and is not installed as the process-global provider.
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
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                export_timeout_millis=int(EXPORTER_TIMEOUT_SECONDS * 1000),
            )
        )
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
    except Exception:
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

    return OTLPSpanExporter(
        endpoint=_traces_url(endpoint),
        timeout=int(EXPORTER_TIMEOUT_SECONDS),
    )


def _traces_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


def _shutdown_provider_bounded(provider: TracerProvider, *, timeout_seconds: float) -> None:
    done = threading.Event()

    def _run() -> None:
        try:
            provider.shutdown()
        except Exception:
            pass
        finally:
            done.set()

    thread = threading.Thread(target=_run, name="dream-otel-shutdown", daemon=True)
    thread.start()
    if not done.wait(timeout=timeout_seconds):
        warnings.warn(
            "OTel provider shutdown exceeded "
            f"{timeout_seconds:.0f}s (collector absent or slow); continuing. "
            "Set OTEL_SDK_DISABLED=true when no collector is running.",
            RuntimeWarning,
            stacklevel=2,
        )


def _shutdown_provider() -> None:
    global _provider, _handle
    if _handle is not None:
        with suppress(Exception):
            _handle.shutdown()
    _provider = None
    _handle = None


__all__ = [
    "EXPORTER_TIMEOUT_SECONDS",
    "SHUTDOWN_TIMEOUT_SECONDS",
    "OtelProviderHandle",
    "build_tracer_provider",
    "get_otel_tracer",
    "reset_otel_provider_for_tests",
]
