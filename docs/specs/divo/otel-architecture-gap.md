# OTEL architecture — gap analysis vs SOTA

Status: implementation baseline for default-on OTLP beside JSONL.
Compared: dream (this repo), chorus, lattice, horizon vs hermes-otel, Paperclip, Arceus plan `#12`.

## Current state

| Layer | What ships today | OTEL? |
|-------|------------------|-------|
| **dream** | `JsonlTracer` — OTel-*shaped* JSONL (`gen_ai.*` attrs, span nesting) plus `CompositeTracer` OTLP fan-out. | JSONL substrate + default-on OTLP |
| **chorus** | `EventBus` + `TraceStamper` (task-lineage `trace_id`). Spec 08 §4 promises opt-in OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. | Spec'd, **not built** on main |
| **lattice / horizon** | No tracing SDK; horizon mirrors chorus EventBus | Absent |
| **Arceus apps** | Activity / Langfuse sinks (TS) | Partial, product-side |

## SOTA reference (hermes-otel / Paperclip)

1. **Env-gated, zero-cost when off** — no import / no TracerProvider unless endpoint (or keys) set.
2. **Real OTLP/HTTP** via `BatchSpanProcessor` (non-blocking queue).
3. **Span hierarchy** — session/agent root → llm → api → tool (hermes); beat → tool → eval (chorus analogue).
4. **GenAI semantic conventions** — `gen_ai.*` (and optionally dual OpenInference `llm.*`).
5. **Force-flush on session/beat end**; graceful degradation if packages missing.
6. Paperclip: boot awaits instrumentation; no-op unless `OTEL_EXPORTER_OTLP_ENDPOINT`.

## Gaps we close in this branch

1. dream: OTel SDK is a **core** dependency; `CompositeTracer` is default-on
   (JSONL + OTLP). Endpoint defaults to `http://localhost:4318`.
2. dream: keep JSONL as durable local substrate; OTLP is always fan-out.
3. chorus: `OtelSpanSink` on EventBus by default; opt out with `OTEL_SDK_DISABLED`.
4. Typed surface only — no `Any` / `dict[str, Any]` / `getattr` in new modules; `AttributeValue` for span attrs.
5. TDD + per-step eval scripts under `evals/otel/`.

Opt-out: `OTEL_SDK_DISABLED=true` (standard OTel env). Endpoint override:
`OTEL_EXPORTER_OTLP_ENDPOINT`.

## Collector-absent shutdown

Default-on OTLP still attempts export when nothing is listening on `:4318`.
Span creation stays non-blocking (`BatchSpanProcessor` enqueue). Process
shutdown is **bounded at 5 seconds** (`SHUTDOWN_TIMEOUT_SECONDS`); the OTLP
HTTP exporter timeout is 2 seconds per attempt. Spans that cannot flush in
that window are dropped. JSONL is unaffected.

This does **not** disable OTLP when a collector is missing — that would change
product intent. Set `OTEL_SDK_DISABLED=true` in environments with no collector
if you do not want export attempts or the shutdown wait.

## Explicit non-goals (this PR series)

- lattice / horizon exporters (consume chorus bus later).
- Multi-backend YAML fan-out (hermes multi-collector) — single OTLP endpoint first.
- Vendor-locked Langfuse SDK (Langfuse remains reachable **via OTLP**).
