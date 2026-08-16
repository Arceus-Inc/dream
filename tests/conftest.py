"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(autouse=True)
def _otel_test_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pytest off the real OTLP HTTP path without disabling the SDK.

    Production is default-on against ``localhost:4318``. A missing collector
    must not add retry delay to every test process, so the HTTP exporter is
    stubbed with an in-memory sink. Tests that assert ``OTEL_SDK_DISABLED``
    still set that env var themselves.
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(
        "dream.observability._otel_provider._build_otlp_exporter",
        lambda endpoint: InMemorySpanExporter(),
    )
