"""Provider shutdown is bounded when the collector is absent or hanging."""

from __future__ import annotations

import time
import warnings
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from dream.observability._otel_config import OtelConfig
from dream.observability._otel_provider import (
    EXPORTER_TIMEOUT_SECONDS,
    SHUTDOWN_TIMEOUT_SECONDS,
    build_tracer_provider,
    get_otel_tracer,
    reset_otel_provider_for_tests,
)


class _HangingExporter(SpanExporter):
    """Blocks far longer than the documented shutdown bound."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        del spans
        time.sleep(30)
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        time.sleep(30)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        del timeout_millis
        time.sleep(30)
        return False


def setup_function() -> None:
    reset_otel_provider_for_tests()


def teardown_function() -> None:
    reset_otel_provider_for_tests()


def _config() -> OtelConfig:
    return OtelConfig(
        enabled=True,
        endpoint="http://127.0.0.1:4318",
        service_name="dream-test",
        service_version="0.0.0",
    )


def test_shutdown_timeout_constants_are_finite() -> None:
    assert SHUTDOWN_TIMEOUT_SECONDS == 5.0
    assert EXPORTER_TIMEOUT_SECONDS == 2.0


def test_shutdown_returns_within_bound_when_exporter_hangs() -> None:
    handle = build_tracer_provider(_config(), span_exporter=_HangingExporter())
    tracer = handle.tracer
    span = tracer.start_span("llm.call")
    span.end()
    start = time.monotonic()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        handle.shutdown(timeout_seconds=0.2)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0
    assert any("OTEL_SDK_DISABLED" in str(item.message) for item in caught)


def test_get_otel_tracer_returns_none_when_disabled() -> None:
    handle = get_otel_tracer(
        config=OtelConfig(
            enabled=False,
            endpoint=None,
            service_name="dream-test",
            service_version="0.0.0",
        )
    )
    assert handle is None
