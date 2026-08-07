"""OTel config — env-gated, zero-cost when off (Paperclip / hermes-otel rule).

This module imports **no** ``opentelemetry`` packages. Callers detect enablement
via :func:`is_otel_enabled` / :func:`load_otel_config` before importing the
provider implementation.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"
_SERVICE_VERSION_ENV = "OTEL_SERVICE_VERSION"
_DEFAULT_SERVICE_NAME = "dream"
_DEFAULT_SERVICE_VERSION = "0.1.0"


@dataclass(frozen=True)
class OtelConfig:
    """Resolved OTel export settings."""

    enabled: bool
    endpoint: str | None
    service_name: str
    service_version: str
    insecure: bool


def is_otel_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """True iff ``OTEL_EXPORTER_OTLP_ENDPOINT`` is a non-empty string."""
    env = environ if environ is not None else os.environ
    endpoint = env.get(_ENDPOINT_ENV)
    return bool(endpoint and endpoint.strip())


def load_otel_config(*, environ: Mapping[str, str] | None = None) -> OtelConfig:
    """Load config from the process environment (or an injected mapping)."""
    env = environ if environ is not None else os.environ
    raw_endpoint = env.get(_ENDPOINT_ENV)
    endpoint = raw_endpoint.strip() if raw_endpoint and raw_endpoint.strip() else None
    service_name = (env.get(_SERVICE_NAME_ENV) or _DEFAULT_SERVICE_NAME).strip() or _DEFAULT_SERVICE_NAME
    service_version = (
        env.get(_SERVICE_VERSION_ENV) or _DEFAULT_SERVICE_VERSION
    ).strip() or _DEFAULT_SERVICE_VERSION
    insecure = endpoint is not None and endpoint.startswith("http://")
    return OtelConfig(
        enabled=endpoint is not None,
        endpoint=endpoint,
        service_name=service_name,
        service_version=service_version,
        insecure=insecure,
    )


__all__ = [
    "OtelConfig",
    "is_otel_enabled",
    "load_otel_config",
]
