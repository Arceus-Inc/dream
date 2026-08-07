"""Step-1 eval: dream OTEL foundation contracts.

Run: ``uv run --extra otel python evals/otel/eval_step1_foundation.py``
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
    # 1. Zero-cost gate: importing the gate module must not pull opentelemetry.
    for key in list(sys.modules):
        if key.startswith("opentelemetry"):
            del sys.modules[key]
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    from dream.observability._otel_config import is_otel_enabled, load_otel_config

    _check("disabled without endpoint", not is_otel_enabled())
    cfg = load_otel_config()
    _check("config disabled", not cfg.enabled)
    _check(
        "no otel import when disabled",
        not any(k.startswith("opentelemetry") for k in sys.modules),
    )

    # 2. Attribute typing is closed (no Any).
    from dream.observability._attributes import AttributeValue, coerce_attributes

    sample: dict[str, AttributeValue] = {"a": 1, "b": "x", "c": True, "d": 1.5}
    coerced = coerce_attributes(sample)
    _check("coerce preserves values", coerced == sample)

    # 3. With endpoint + otel extra, provider builds and InMemory exporter sees spans.
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:4318"
    try:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from dream.observability._otel_provider import (
            OtelProviderHandle,
            build_tracer_provider,
            reset_otel_provider_for_tests,
        )
        from dream.observability._otel_tracer import OtelTracer

        reset_otel_provider_for_tests()
        memory = InMemorySpanExporter()
        handle = build_tracer_provider(
            load_otel_config(),
            span_exporter=memory,
            service_name="dream-eval",
            service_version="0.0.0-eval",
        )
        _check("provider handle enabled", isinstance(handle, OtelProviderHandle) and handle.enabled)
        tracer = OtelTracer(
            handle.tracer,
            session_id="sess-eval",
            task_id="task-eval",
        )
        with tracer.span("llm.call", {"gen_ai.request.model": "eval-model"}):
            tracer.event("tool.result", {"ok": True})
        handle.force_flush()
        spans = memory.get_finished_spans()
        _check("emitted at least one span", len(spans) >= 1, f"got {len(spans)}")
        names = {s.name for s in spans}
        _check("llm.call span present", "llm.call" in names, str(names))
    except ImportError as exc:
        _check("otel extra installed", False, str(exc))
    finally:
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        try:
            from dream.observability._otel_provider import reset_otel_provider_for_tests

            reset_otel_provider_for_tests()
        except ImportError:
            pass

    print("eval_step1_foundation: all checks passed")


if __name__ == "__main__":
    main()
