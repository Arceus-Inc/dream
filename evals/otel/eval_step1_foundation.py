"""Step-1 eval: dream OTEL is default-on.

Run: ``uv run python evals/otel/eval_step1_foundation.py``
Exit 0 iff all checks pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{status}] {name}{suffix}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    os.environ.pop("OTEL_SDK_DISABLED", None)
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

    from dream.observability._otel_config import is_otel_enabled, load_otel_config

    _check("enabled by default", is_otel_enabled())
    cfg = load_otel_config()
    _check("default endpoint localhost", cfg.endpoint == "http://localhost:4318")

    os.environ["OTEL_SDK_DISABLED"] = "true"
    _check("disabled via flag", not is_otel_enabled())
    os.environ.pop("OTEL_SDK_DISABLED", None)

    from dream.observability._attributes import AttributeValue, coerce_attributes

    sample: dict[str, AttributeValue] = {"a": 1, "b": "x", "c": True, "d": 1.5}
    _check("coerce preserves values", coerce_attributes(sample) == sample)

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from dream.observability._otel_provider import (
        SHUTDOWN_TIMEOUT_SECONDS,
        OtelProviderHandle,
        build_tracer_provider,
        reset_otel_provider_for_tests,
    )
    from dream.observability._otel_tracer import OtelTracer

    _check("shutdown bound is five seconds", SHUTDOWN_TIMEOUT_SECONDS == 5.0)

    reset_otel_provider_for_tests()
    memory = InMemorySpanExporter()
    handle = build_tracer_provider(
        load_otel_config(),
        span_exporter=memory,
        service_name="dream-eval",
        service_version="0.0.0-eval",
    )
    _check("provider handle enabled", isinstance(handle, OtelProviderHandle) and handle.enabled)
    tracer = OtelTracer(handle.tracer, session_id="sess-eval", task_id="task-eval")
    with tracer.span("llm.call", {"gen_ai.request.model": "eval-model"}):
        tracer.event("tool.result", {"ok": True})
    handle.force_flush()
    spans = memory.get_finished_spans()
    _check("emitted at least one span", len(spans) >= 1, f"got {len(spans)}")
    names = {s.name for s in spans}
    _check("llm.call span present", "llm.call" in names, str(names))
    reset_otel_provider_for_tests()

    print("eval_step1_foundation: all checks passed")


if __name__ == "__main__":
    main()
