"""Spec 12a — TraceEvent envelope, JSONL codec, and OTel attribute helpers."""

from __future__ import annotations

import json

import pytest

from dream.observability._events import (
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    TraceEvent,
    from_jsonl_line,
    llm_call_attrs,
    state_transition_attrs,
    to_jsonl_line,
    tool_call_attrs,
    tool_result_attrs,
    validator_finding_attrs,
)


def _event(**kw: object) -> TraceEvent:
    base: dict[str, object] = {
        "ts": "2026-06-07T00:00:00.000Z",
        "session_id": "s1",
        "task_id": "T1",
        "event_type": "llm.call",
        "span_id": "span_a",
        "parent_span_id": None,
        "attributes": {},
    }
    base.update(kw)
    return TraceEvent(**base)  # type: ignore[arg-type]


def test_codec_roundtrips_full_envelope() -> None:
    event = _event(
        event_type="tool.call",
        span_id="span_b",
        parent_span_id="span_a",
        attributes={"tool.name": "bash", "duration_ms": 12},
    )
    line = to_jsonl_line(event)
    assert "\n" not in line
    assert from_jsonl_line(line) == event


def test_codec_preserves_null_task_and_parent() -> None:
    event = _event(task_id=None, parent_span_id=None)
    assert from_jsonl_line(to_jsonl_line(event)) == event


def test_to_jsonl_line_emits_known_envelope_keys() -> None:
    payload = json.loads(to_jsonl_line(_event()))
    assert set(payload) == {
        "ts",
        "session_id",
        "task_id",
        "event_type",
        "span_id",
        "parent_span_id",
        "attributes",
    }


def test_from_jsonl_line_rejects_unknown_event_type() -> None:
    bad = to_jsonl_line(_event()).replace('"llm.call"', '"bogus.event"')
    with pytest.raises(ValueError):
        from_jsonl_line(bad)


def test_llm_call_attrs_use_otel_genai_names() -> None:
    attrs = llm_call_attrs(
        system="openai", model="gpt-4o", prompt_tokens=10, completion_tokens=5
    )
    assert attrs[GEN_AI_SYSTEM] == "openai"
    assert attrs[GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert attrs[GEN_AI_USAGE_PROMPT_TOKENS] == 10
    assert attrs[GEN_AI_USAGE_COMPLETION_TOKENS] == 5


def test_tool_call_attrs_carry_name_and_read_only() -> None:
    attrs = tool_call_attrs(tool_name="bash", is_read_only=True)
    assert attrs["tool.name"] == "bash"
    assert attrs["tool.read_only"] is True


def test_tool_result_attrs_carry_error_and_offload() -> None:
    attrs = tool_result_attrs(
        tool_name="bash", is_error=True, offloaded=True, offload_ref="ptr.txt"
    )
    assert attrs["tool.name"] == "bash"
    assert attrs["tool.is_error"] is True
    assert attrs["tool.offloaded"] is True
    assert attrs["tool.offload_ref"] == "ptr.txt"


def test_validator_finding_attrs_carry_severity_code_message() -> None:
    attrs = validator_finding_attrs(severity="warning", code="x", message="m", path="p")
    assert attrs == {
        "finding.severity": "warning",
        "finding.code": "x",
        "finding.message": "m",
        "finding.path": "p",
    }


def test_validator_finding_attrs_omits_absent_path() -> None:
    attrs = validator_finding_attrs(severity="info", code="c", message="m")
    assert "finding.path" not in attrs


def test_state_transition_attrs_carry_from_to() -> None:
    attrs = state_transition_attrs(kind="turn", from_state="read", to_state="plan")
    assert attrs == {
        "transition.kind": "turn",
        "transition.from": "read",
        "transition.to": "plan",
    }
