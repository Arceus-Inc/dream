"""Trace event envelope, JSONL codec, and OTel-GenAI attribute helpers (Spec 12a).

One uniform line per event — ``ts``/``session_id``/``task_id``/``event_type``/
``span_id``/``parent_span_id`` plus a free-form ``attributes`` map (the OTel
attribute bag; OpenHarness's ``RepoJournalEntry`` metadata bag is the closest
shipped analogue). Attribute *keys* are centralised here as constants + small
builders so no emitter hand-types ``gen_ai.*`` strings (AC #16).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, get_args

TraceEventType = Literal[
    "llm.call",
    "tool.call",
    "tool.result",
    "validator.finding",
    "state.transition",
    "evaluation.record",
]

_EVENT_TYPES: frozenset[str] = frozenset(get_args(TraceEventType))

# --- OTel GenAI attribute keys (centralised; see AC #16) --------------------
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
GEN_AI_USAGE_CACHE_READ_TOKENS = "gen_ai.usage.cache_read_tokens"


@dataclass(frozen=True)
class TraceEvent:
    """One OTel-shaped trace line."""

    ts: str
    session_id: str
    task_id: str | None
    event_type: TraceEventType
    span_id: str
    parent_span_id: str | None
    attributes: Mapping[str, object]


def to_jsonl_line(event: TraceEvent) -> str:
    payload = {
        "ts": event.ts,
        "session_id": event.session_id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "attributes": dict(event.attributes),
    }
    return json.dumps(payload, separators=(",", ":"), default=str)


def from_jsonl_line(line: str) -> TraceEvent:
    data = json.loads(line)
    event_type = data["event_type"]
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"unknown trace event_type: {event_type!r}")
    return TraceEvent(
        ts=data["ts"],
        session_id=data["session_id"],
        task_id=data.get("task_id"),
        event_type=cast(TraceEventType, event_type),
        span_id=data["span_id"],
        parent_span_id=data.get("parent_span_id"),
        attributes=data.get("attributes", {}),
    )


# --- attribute builders (one place that knows the key strings) --------------


def llm_call_attrs(
    *,
    system: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    duration_ms: int | None = None,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        GEN_AI_SYSTEM: system,
        GEN_AI_REQUEST_MODEL: model,
        GEN_AI_USAGE_PROMPT_TOKENS: prompt_tokens,
        GEN_AI_USAGE_COMPLETION_TOKENS: completion_tokens,
        GEN_AI_USAGE_CACHE_READ_TOKENS: cache_read_tokens,
    }
    if duration_ms is not None:
        attrs["duration_ms"] = duration_ms
    return attrs


def tool_call_attrs(
    *, tool_name: str, is_read_only: bool | None = None, duration_ms: int | None = None
) -> dict[str, object]:
    attrs: dict[str, object] = {"tool.name": tool_name}
    if is_read_only is not None:
        attrs["tool.read_only"] = is_read_only
    if duration_ms is not None:
        attrs["duration_ms"] = duration_ms
    return attrs


def tool_result_attrs(
    *,
    tool_name: str,
    is_error: bool,
    offloaded: bool = False,
    offload_ref: str | None = None,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "tool.name": tool_name,
        "tool.is_error": is_error,
        "tool.offloaded": offloaded,
    }
    if offload_ref is not None:
        attrs["tool.offload_ref"] = offload_ref
    return attrs


def validator_finding_attrs(
    *, severity: str, code: str, message: str, path: str | None = None
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "finding.severity": severity,
        "finding.code": code,
        "finding.message": message,
    }
    if path is not None:  # omit when absent (OTel convention; lets consumers test presence)
        attrs["finding.path"] = path
    return attrs


def state_transition_attrs(*, kind: str, from_state: str, to_state: str) -> dict[str, object]:
    return {
        "transition.kind": kind,
        "transition.from": from_state,
        "transition.to": to_state,
    }


def evaluation_record_attrs(
    *, outcome: str, weighted_total: float, rubric_version: str, evaluator_version: str
) -> dict[str, object]:
    """Schema for the evaluator's verdict event (emitted by 12d, not here)."""
    return {
        "evaluation.outcome": outcome,
        "evaluation.weighted_total": weighted_total,
        "evaluation.rubric_version": rubric_version,
        "evaluation.evaluator_version": evaluator_version,
    }


__all__ = [
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_SYSTEM",
    "GEN_AI_USAGE_CACHE_READ_TOKENS",
    "GEN_AI_USAGE_COMPLETION_TOKENS",
    "GEN_AI_USAGE_PROMPT_TOKENS",
    "TraceEvent",
    "TraceEventType",
    "evaluation_record_attrs",
    "from_jsonl_line",
    "llm_call_attrs",
    "state_transition_attrs",
    "to_jsonl_line",
    "tool_call_attrs",
    "tool_result_attrs",
    "validator_finding_attrs",
]
