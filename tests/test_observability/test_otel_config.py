"""OTel config — default-on; opt out via OTEL_SDK_DISABLED."""

from __future__ import annotations

import sys

from dream.observability._otel_config import is_otel_enabled, load_otel_config


def test_enabled_by_default() -> None:
    assert is_otel_enabled(environ={})
    cfg = load_otel_config(environ={})
    assert cfg.enabled is True
    assert cfg.endpoint == "http://localhost:4318"
    assert cfg.service_name == "dream"


def test_disabled_via_sdk_flag() -> None:
    env = {"OTEL_SDK_DISABLED": "true"}
    assert not is_otel_enabled(environ=env)
    cfg = load_otel_config(environ=env)
    assert cfg.enabled is False
    assert cfg.endpoint is None


def test_disabled_accepts_truthy_aliases() -> None:
    for raw in ("1", "TRUE", "Yes", "on"):
        assert not is_otel_enabled(environ={"OTEL_SDK_DISABLED": raw})


def test_explicit_endpoint_override() -> None:
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example/v1/traces"}
    cfg = load_otel_config(environ=env)
    assert cfg.enabled is True
    assert cfg.endpoint == "https://otel.example/v1/traces"


def test_service_name_override() -> None:
    cfg = load_otel_config(
        environ={
            "OTEL_SERVICE_NAME": "dream-test",
            "OTEL_SERVICE_VERSION": "9.9.9",
        }
    )
    assert cfg.service_name == "dream-test"
    assert cfg.service_version == "9.9.9"


def test_config_module_does_not_import_opentelemetry() -> None:
    for key in list(sys.modules):
        if key.startswith("opentelemetry"):
            del sys.modules[key]
    import importlib

    import dream.observability._otel_config as mod

    importlib.reload(mod)
    assert not any(k.startswith("opentelemetry") for k in sys.modules)
