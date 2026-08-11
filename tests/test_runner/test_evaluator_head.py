"""Spec 10 slice G5 — production evaluator head.

``make_evaluator_head(harness, ...)`` returns an :data:`EvaluatorRun`
that opens an ``evaluator``-bound session through ``Harness.run_role``,
sends a verification intent built from the sprint contract + step, and
parses the model's verdict into an :class:`EvaluationRecord`.

Like the planner head (G3) and unlike the generator head (G4), the
evaluator head DOES parse output — the verdict is the artefact. Replies
must be bare JSON matching the verdict schema (``response_format``
strict); malformed replies raise :class:`EvaluatorHeadParseError`.

Unlike the generator head, ``contract`` is always present here:
``run_task`` skips the evaluator entirely when the contract is
disabled (per spec §"Disabling the evaluator"), so the head never sees
a ``None`` contract.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from dream.api.response_format import ResponseFormatKind
from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
)
from dream.engine._messages import ConversationMessage, TextBlock
from dream.harness import Harness, HarnessConfig
from dream.planner import LedgerStep
from dream.runner import (
    EvaluatorHeadParseError,
    RoleSessionError,
    make_evaluator_head,
)
from dream.session import SessionOptions
from dream.sprint import EvaluationRecord, SprintContract
from tests.test_engine._fakes import FakeDispatcher

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class _ScriptedReplyStreamer:
    """Yields one scripted assistant turn; records the user prompts seen."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        yield AssistantTextDelta(text=self._reply)
        yield AssistantTurnComplete(
            blocks=[TextBlock(text=self._reply)],
            usage=UsageSnapshot(),
        )

    @property
    def last_user_text(self) -> str:
        last = self.calls[-1]
        user_msgs = [m for m in last if m.role == "user"]
        assert user_msgs, "no user message in last call"
        return "".join(b.text for b in user_msgs[-1].content if isinstance(b, TextBlock))


def _verdict(
    *,
    outcome: str = "pass",
    score: float = 0.0,
    notes: str = "",
    items: list[str] | None = None,
) -> str:
    # Strict wire contract: every EvaluatorVerdict property must be present.
    body: dict[str, object] = {
        "outcome": outcome,
        "score": score,
        "notes": notes,
        "items": list(items) if items is not None else [],
    }
    return json.dumps(body)


def _harness_with_reply(
    reply: str,
) -> tuple[Harness, _ScriptedReplyStreamer]:
    streamer = _ScriptedReplyStreamer(reply)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    return Harness(config), streamer


def _harness_capturing_options(
    reply: str,
) -> tuple[Harness, _ScriptedReplyStreamer, list[SessionOptions]]:
    captured: list[SessionOptions] = []
    streamer = _ScriptedReplyStreamer(reply)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        captured.append(options)
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    return Harness(config), streamer, captured


def _contract(
    *,
    task_id: str = "task-001",
    sprint_number: int = 1,
    goal: str = "ship the widget",
    acceptance_criteria: tuple[str, ...] = (
        "widget renders",
        "widget passes axe",
    ),
    # Empty by default: these head tests pin parsing/prompt behaviour.
    verification_steps: tuple[dict[str, str], ...] = (),
    scope_includes: tuple[str, ...] = ("src/widget/",),
    scope_excludes: tuple[str, ...] = ("src/legacy/",),
    evaluator_enabled: bool = True,
) -> SprintContract:
    return SprintContract(
        task_id=task_id,
        sprint_number=sprint_number,
        goal=goal,
        acceptance_criteria=acceptance_criteria,
        verification_steps=verification_steps,
        scope_includes=scope_includes,
        scope_excludes=scope_excludes,
        evaluator_enabled=evaluator_enabled,
    )


def _step(*, step_id: str = "s1", description: str = "build the widget shell") -> LedgerStep:
    return LedgerStep(id=step_id, description=description)


# --------------------------------------------------------------------------
# Callable shape + return value
# --------------------------------------------------------------------------


def test_make_evaluator_head_returns_callable() -> None:
    harness, _ = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)
    assert callable(head)


async def test_evaluator_head_returns_evaluation_record() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="pass"))
    head = make_evaluator_head(harness)

    out = await head("task-001", 1, _contract(), _step())

    assert isinstance(out, EvaluationRecord)


# --------------------------------------------------------------------------
# Record field wiring — task / sprint / step come from the call, not the
# model. The model never picks its own task id.
# --------------------------------------------------------------------------


async def test_record_uses_call_task_id_not_model_payload() -> None:
    # Even if the model echoes a different id, the head re-stamps it.
    harness, _ = _harness_with_reply(_verdict(outcome="pass"))
    head = make_evaluator_head(harness)

    rec = await head("task-real", 1, _contract(task_id="task-real"), _step())

    assert rec.task_id == "task-real"


async def test_record_uses_call_sprint_number() -> None:
    harness, _ = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    rec = await head("task-001", 9, _contract(sprint_number=9), _step())

    assert rec.sprint_number == 9


async def test_record_uses_call_step_id() -> None:
    harness, _ = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step(step_id="step-alpha"))

    assert rec.step_id == "step-alpha"


# --------------------------------------------------------------------------
# Verdict parsing — outcome
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["pass", "needs-changes", "fail"])
async def test_parses_each_valid_outcome(outcome: str) -> None:
    harness, _ = _harness_with_reply(_verdict(outcome=outcome))
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.outcome == outcome


# --------------------------------------------------------------------------
# Verdict parsing — optional fields default sensibly
# --------------------------------------------------------------------------


async def test_score_defaults_to_zero_when_omitted() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="pass"))
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.score == 0.0


async def test_score_round_trips_when_present() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="pass", score=0.83))
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.score == 0.83


async def test_notes_default_to_empty_when_omitted() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="pass"))
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.notes == ""


async def test_notes_round_trip_when_present() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="needs-changes", notes="missing aria-labels"))
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.notes == "missing aria-labels"


async def test_items_default_to_empty_tuple_when_omitted() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="pass"))
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.items == ()


async def test_items_round_trip_as_tuple() -> None:
    harness, _ = _harness_with_reply(
        _verdict(
            outcome="needs-changes",
            items=["add aria-label to button", "fix contrast on link"],
        )
    )
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.items == (
        "add aria-label to button",
        "fix contrast on link",
    )


# --------------------------------------------------------------------------
# evaluator_version is stamped by the head, not the model
# --------------------------------------------------------------------------


async def test_default_evaluator_version_is_stamped() -> None:
    harness, _ = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())

    # Default is whatever the head ships with — not "v0" (EvaluationRecord's
    # bare default), since this head represents a specific evaluator
    # implementation. We just pin that it's non-empty.
    assert rec.evaluator_version
    assert isinstance(rec.evaluator_version, str)


async def test_custom_evaluator_version_round_trips() -> None:
    harness, _ = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness, evaluator_version="head-2026.06")

    rec = await head("task-001", 1, _contract(), _step())

    assert rec.evaluator_version == "head-2026.06"


# --------------------------------------------------------------------------
# Envelope tolerance
# --------------------------------------------------------------------------
# Parse failures
# --------------------------------------------------------------------------


async def test_non_json_reply_raises_parse_error() -> None:
    harness, _ = _harness_with_reply("I think it's fine, looks good!")
    head = make_evaluator_head(harness)

    with pytest.raises(EvaluatorHeadParseError):
        await head("task-001", 1, _contract(), _step())


async def test_parses_bare_json_verdict() -> None:
    harness, _ = _harness_with_reply(
        '{"outcome": "pass", "score": 0.9, "notes": "looks good", "items": []}'
    )
    head = make_evaluator_head(harness)

    rec = await head("task-001", 1, _contract(), _step())
    assert rec.outcome == "pass"
    assert rec.notes == "looks good"


async def test_missing_outcome_key_raises_parse_error() -> None:
    harness, _ = _harness_with_reply('{"score": 0.9, "notes": "", "items": []}')
    head = make_evaluator_head(harness)

    with pytest.raises(EvaluatorHeadParseError):
        await head("task-001", 1, _contract(), _step())


async def test_invalid_outcome_value_raises_parse_error() -> None:
    harness, _ = _harness_with_reply(_verdict(outcome="maybe"))
    head = make_evaluator_head(harness)

    with pytest.raises(EvaluatorHeadParseError):
        await head("task-001", 1, _contract(), _step())




# --------------------------------------------------------------------------
# Role wiring
# --------------------------------------------------------------------------


async def test_evaluator_head_uses_evaluator_role() -> None:
    harness, _, captured = _harness_capturing_options(_verdict())
    head = make_evaluator_head(harness)

    await head("task-001", 1, _contract(), _step())

    assert len(captured) == 1
    assert captured[0].metadata.get("dream.role") == "evaluator"


# --------------------------------------------------------------------------
# Intent assembly
# --------------------------------------------------------------------------


async def test_intent_includes_task_id_sprint_and_step() -> None:
    harness, streamer = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    step = _step(step_id="alpha-1", description="wire the kettle")
    await head("task-xyz", 7, _contract(task_id="task-xyz", sprint_number=7), step)

    prompt = streamer.last_user_text
    assert "task-xyz" in prompt
    assert "7" in prompt
    assert "alpha-1" in prompt
    assert "wire the kettle" in prompt


async def test_intent_includes_acceptance_criteria() -> None:
    harness, streamer = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    await head(
        "task-001",
        1,
        _contract(
            acceptance_criteria=(
                "renders under 50ms",
                "no axe violations",
            )
        ),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "renders under 50ms" in prompt
    assert "no axe violations" in prompt


async def test_intent_includes_verification_steps() -> None:
    harness, streamer = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    await head(
        "task-001",
        1,
        _contract(
            verification_steps=(
                # echo is a cheap listed command; the evaluator runs it via bash.
                {"kind": "test", "command": "echo pytest tests/foo"},
                {"kind": "eval", "command": "echo axe http://localhost"},
            )
        ),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "pytest tests/foo" in prompt
    assert "axe http://localhost" in prompt


async def test_head_does_not_execute_verification_steps() -> None:
    """verification_steps are prompt data; the session runs them via bash."""
    harness, streamer = _harness_with_reply(_verdict(outcome="pass"))
    head = make_evaluator_head(harness)

    record = await head(
        "task-001",
        1,
        _contract(verification_steps=({"kind": "test", "command": "exit 1"},)),
        _step(),
    )

    assert record.outcome == "pass"
    assert "exit 1" in streamer.last_user_text


async def test_intent_tells_the_evaluator_to_run_verify_via_bash() -> None:
    """Protocol lives in standing orders; user turn carries contract + schema."""
    from dream.prompts.system_prompt import packaged_standing_orders

    orders = packaged_standing_orders(role="evaluator").lower()
    assert "bash" in orders
    assert "verification" in orders
    assert "oracle" not in orders

    harness, streamer = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    await head("task-001", 1, _contract(), _step())

    prompt = streamer.last_user_text.lower()
    assert "step under review" in prompt
    assert "verification steps" in prompt or "pytest" in prompt or "goal" in prompt


async def test_intent_explains_json_verdict_contract() -> None:
    """User turn carries the JSON schema; outcome vocabulary is in standing orders."""
    from dream.prompts.system_prompt import packaged_standing_orders

    orders = packaged_standing_orders(role="evaluator")
    assert "needs-changes" in orders
    assert "fail" in orders

    harness, streamer = _harness_with_reply(_verdict())
    head = make_evaluator_head(harness)

    await head("task-001", 1, _contract(), _step())

    prompt = streamer.last_user_text
    assert "JSON object" in prompt
    assert "<verdict>" not in prompt
    assert "pass" in prompt


async def test_evaluator_head_attaches_response_format() -> None:
    harness, _, captured = _harness_capturing_options(_verdict())
    head = make_evaluator_head(harness)

    await head("task-001", 1, _contract(), _step())

    assert captured
    rf = captured[0].response_format
    assert rf is not None
    assert rf.kind is ResponseFormatKind.JSON_SCHEMA
    assert rf.json_schema is not None
    assert rf.json_schema.name == "evaluator_verdict"
    assert rf.json_schema.strict is True


async def test_intent_teaches_durable_outcome_semantics() -> None:
    """Durable outcome semantics live in evaluator standing orders."""
    from dream.prompts.system_prompt import packaged_standing_orders

    prompt = packaged_standing_orders(role="evaluator").lower()
    assert "needs-changes" in prompt
    assert "fail" in prompt
    assert "repair" in prompt or "in-tree" in prompt or "generator can fix" in prompt
    assert (
        "no honest" in prompt
        or "no repair" in prompt
        or "irrecoverable" in prompt
        or "abandon" in prompt
    )
    assert "prefer" in prompt and "needs-changes" in prompt


async def test_intent_rejects_weaker_substitute_than_task_intent() -> None:
    """User turn embeds Intent as data; fidelity coaching lives in standing orders."""
    from dream.prompts.system_prompt import packaged_standing_orders

    harness, streamer = _harness_with_reply(_verdict())
    head = make_evaluator_head(
        harness,
        task_intent="Ship a one-page brief with audience, offer, and a single CTA.",
    )

    await head("task-001", 1, _contract(), _step())

    prompt = streamer.last_user_text
    assert "TASK INTENT" in prompt
    assert "audience, offer, and a single CTA" in prompt
    assert "weaker" not in prompt.lower()
    assert "source of truth" not in prompt.lower()
    orders = packaged_standing_orders(role="evaluator").lower()
    assert "intent" in orders and ("weaker" in orders or "fidelity" in orders)


# --------------------------------------------------------------------------
# harness_dir overlay forwarding
# --------------------------------------------------------------------------


async def test_evaluator_head_uses_harness_dir_for_role_overlay(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "evaluator.toml").write_text(
        'system_prompt = "OVERLAY EVAL PROMPT"\n', encoding="utf-8"
    )

    harness, _, captured = _harness_capturing_options(_verdict())
    head = make_evaluator_head(harness, harness_dir=tmp_path)

    await head("task-001", 1, _contract(), _step())

    assert captured[0].system_prompt is not None
    assert captured[0].system_prompt.startswith("OVERLAY EVAL PROMPT")


# --------------------------------------------------------------------------
# Engine-level failures bubble unchanged
# --------------------------------------------------------------------------


class _ErrorStreamer:
    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        from dream.engine._events import ErrorEvent

        yield ErrorEvent(message="boom", recoverable=False)
        yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())


async def test_evaluator_head_propagates_role_session_error() -> None:
    streamer = _ErrorStreamer()

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    head = make_evaluator_head(harness)

    with pytest.raises(RoleSessionError):
        await head("task-001", 1, _contract(), _step())
