"""OTel config — on by default; opt out with ``OTEL_SDK_DISABLED=true``.

Default exporter endpoint is ``http://localhost:4318`` when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset. This module imports **no**
``opentelemetry`` packages.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_DISABLED_ENV = "OTEL_SDK_DISABLED"
_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"
_SERVICE_VERSION_ENV = "OTEL_SERVICE_VERSION"
_DEFAULT_ENDPOINT = "http://localhost:4318"
_DEFAULT_SERVICE_NAME = "dream"
_DEFAULT_SERVICE_VERSION = "0.1.0"


@dataclass(frozen=True)
class OtelConfig:
    """Resolved OTel export settings."""

    enabled: bool
    endpoint: str | None
    service_name: str
    service_version: str


def is_otel_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """True unless ``OTEL_SDK_DISABLED`` is a truthy value (``true``/``1``/``yes``)."""
    env = environ if environ is not None else os.environ
    return not _is_disabled(env.get(_DISABLED_ENV))


def load_otel_config(*, environ: Mapping[str, str] | None = None) -> OtelConfig:
    """Load config from the process environment (or an injected mapping)."""
    env = environ if environ is not None else os.environ
    enabled = not _is_disabled(env.get(_DISABLED_ENV))
    raw_endpoint = env.get(_ENDPOINT_ENV)
    if raw_endpoint and raw_endpoint.strip():
        endpoint: str | None = raw_endpoint.strip()
    elif enabled:
        endpoint = _DEFAULT_ENDPOINT
    else:
        endpoint = None
    service_name = (
        env.get(_SERVICE_NAME_ENV) or _DEFAULT_SERVICE_NAME
    ).strip() or _DEFAULT_SERVICE_NAME
    service_version = (
        env.get(_SERVICE_VERSION_ENV) or _DEFAULT_SERVICE_VERSION
    ).strip() or _DEFAULT_SERVICE_VERSION
    return OtelConfig(
        enabled=enabled,
        endpoint=endpoint,
        service_name=service_name,
        service_version=service_version,
    )


def _is_disabled(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "OtelConfig",
    "is_otel_enabled",
    "load_otel_config",
]
