"""The subagent output-schema guardrail — coerce, validate, repair-loop, fail-open.

The pure pieces (coerce/validate) are model-free; the orchestrator drives a bounded reformat loop
through a fake harness and, when it can't produce valid JSON, fails *open* with a warning.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dream.subagents._declaration import Subagent
from dream.subagents._output_guard import (
    MAX_OUTPUT_REPAIRS,
    coerce_json,
    enforce_output_schema,
    validate_output,
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["answer"],
    "additionalProperties": True,
}


class TestCoerceJson:
    def test_plain_object(self) -> None:
        assert coerce_json('{"answer": "hi"}') == {"answer": "hi"}

    def test_fenced_object(self) -> None:
        assert coerce_json('```json\n{"answer": "hi"}\n```') == {"answer": "hi"}

    def test_object_amid_prose(self) -> None:
        text = 'Here is the result:\n{"answer": "hi", "confidence": 0.5}\nThanks!'
        assert coerce_json(text) == {"answer": "hi", "confidence": 0.5}

    def test_non_json_returns_none(self) -> None:
        assert coerce_json("I could not find anything useful.") is None


class TestValidateOutput:
    def test_valid_returns_no_errors(self) -> None:
        assert validate_output({"answer": "x", "confidence": 0.5}, _SCHEMA) == []

    def test_missing_required_field_is_an_error(self) -> None:
        errors = validate_output({"confidence": 0.5}, _SCHEMA)
        assert errors and any("answer" in e for e in errors)

    def test_wrong_type_is_an_error(self) -> None:
        errors = validate_output({"answer": 123}, _SCHEMA)
        assert errors  # answer must be a string


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.final_text = text


class _FakeHarness:
    """Records the reformat prompts and replays a scripted sequence of final_texts."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls = 0
        self.last_options: Any = None

    async def run_role(self, manifest: Any, intent: str, *, options: Any = None) -> _FakeResult:
        self.last_options = options
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return _FakeResult(reply)


def _agent() -> Subagent:
    return Subagent(
        name="web_research", description="d", tools=("web_search",), output_schema=_SCHEMA
    )


@pytest.mark.asyncio
async def test_valid_output_passes_untouched_no_harness_call() -> None:
    harness = _FakeHarness(replies=["unused"])
    out, warning = await enforce_output_schema(
        '{"answer": "grounded", "confidence": 0.8}',
        agent=_agent(),
        harness=harness,  # type: ignore[arg-type]
    )
    assert warning is None
    assert json.loads(out) == {"answer": "grounded", "confidence": 0.8}
    assert harness.calls == 0  # already valid → no reformat


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_then_passes() -> None:
    # First output is missing 'answer'; the (fake) reformat returns a valid object.
    harness = _FakeHarness(replies=['{"answer": "fixed", "confidence": 0.4}'])
    out, warning = await enforce_output_schema(
        '{"confidence": 0.4}',
        agent=_agent(),
        harness=harness,  # type: ignore[arg-type]
    )
    assert warning is None
    assert json.loads(out)["answer"] == "fixed"
    assert harness.calls == 1  # one repair pass sufficed


@pytest.mark.asyncio
async def test_exhausted_repairs_fail_open_with_warning() -> None:
    # The child never produces valid JSON; guardrail fails open with best-effort + warning.
    harness = _FakeHarness(replies=['{"confidence": 0.4}'])  # always missing 'answer'
    out, warning = await enforce_output_schema(
        '{"confidence": 0.4}',
        agent=_agent(),
        harness=harness,  # type: ignore[arg-type]
    )
    assert warning is not None and "best-effort" in warning
    assert json.loads(out) == {"confidence": 0.4}  # best-effort: the last parseable object
    assert harness.calls == MAX_OUTPUT_REPAIRS  # tried the full budget


@pytest.mark.asyncio
async def test_strict_exhausted_repairs_raise() -> None:
    from dream.subagents._output_guard import OutputSchemaError

    agent = Subagent(
        name="api_verifier",
        description="d",
        tools=("bash",),
        output_schema=_SCHEMA,
        strict=True,
    )
    harness = _FakeHarness(replies=['{"confidence": 0.4}'])
    with pytest.raises(OutputSchemaError, match="schema"):
        await enforce_output_schema(
            '{"confidence": 0.4}',
            agent=agent,
            harness=harness,  # type: ignore[arg-type]
        )
    assert harness.calls == MAX_OUTPUT_REPAIRS


@pytest.mark.asyncio
async def test_repair_passes_response_format() -> None:
    harness = _FakeHarness(replies=['{"answer": "fixed"}'])
    await enforce_output_schema(
        '{"confidence": 0.4}',
        agent=_agent(),
        harness=harness,  # type: ignore[arg-type]
    )
    assert harness.last_options is not None
    assert harness.last_options.response_format is not None
    assert harness.last_options.response_format["type"] == "json_schema"


@pytest.mark.asyncio
async def test_unparseable_output_fails_open_with_original_text() -> None:
    harness = _FakeHarness(replies=["still not json"])
    out, warning = await enforce_output_schema(
        "not json at all",
        agent=_agent(),
        harness=harness,  # type: ignore[arg-type]
    )
    assert warning is not None
    assert out == "not json at all"  # best-effort falls back to the original text
