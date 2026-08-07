"""OTel config — zero-cost gate (no SDK import)."""

from __future__ import annotations

import sys

from dream.observability._otel_config import is_otel_enabled, load_otel_config


def test_disabled_when_endpoint_unset() -> None:
    assert not is_otel_enabled(environ={})
    cfg = load_otel_config(environ={})
    assert cfg.enabled is False
    assert cfg.endpoint is None


def test_enabled_when_endpoint_set() -> None:
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
    assert is_otel_enabled(environ=env)
    cfg = load_otel_config(environ=env)
    assert cfg.enabled is True
    assert cfg.endpoint == "http://127.0.0.1:4318"
    assert cfg.insecure is True
    assert cfg.service_name == "dream"


def test_service_name_override() -> None:
    cfg = load_otel_config(
        environ={
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example/v1/traces",
            "OTEL_SERVICE_NAME": "dream-test",
            "OTEL_SERVICE_VERSION": "9.9.9",
        }
    )
    assert cfg.service_name == "dream-test"
    assert cfg.service_version == "9.9.9"
    assert cfg.insecure is False


def test_config_module_does_not_import_opentelemetry() -> None:
    # Ensure a fresh import path: config must stay SDK-free.
    for key in list(sys.modules):
        if key.startswith("opentelemetry"):
            del sys.modules[key]
    import importlib

    import dream.observability._otel_config as mod

    importlib.reload(mod)
    assert not any(k.startswith("opentelemetry") for k in sys.modules)
