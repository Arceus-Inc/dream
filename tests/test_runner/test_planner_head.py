"""Spec 10 slice G3 — production planner head.

``make_planner_head(harness, ...)`` returns a :data:`PlannerCallable`
that opens a ``planner``-bound session through ``Harness.run_role`` and
parses the model's reply into the two artefacts ``run_planner`` writes
to the worktree.

What this slice pins:

- The callable shape matches ``PlannerCallable`` (``(task_id, intent) ->
  Awaitable[PlannerOutput]``) so ``run_planner`` accepts it without
  adaptation.
- The model is asked for a strict ``<spec>...</spec>`` + ``<ledger>...
  </ledger>`` envelope; the parser is tolerant of an inner ```json fence
  the model loves to add.
- The returned ledger carries the caller's ``task_id`` + ``intent`` and
  a fresh ``created_at`` — ``run_planner`` overrides ``task_id`` again
  defensively, but the head owns the rest.
- Parse failures surface as ``PlannerHeadParseError`` (caller can
  retry / escalate); ``RoleSessionError`` from the session layer
  propagates unchanged.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
)
from dream.engine._messages import ConversationMessage, TextBlock
from dream.harness import Harness, HarnessConfig
from dream.planner import LedgerStep, PlannerOutput
from dream.runner import (
    PlannerHeadParseError,
    RoleSessionError,
    make_planner_head,
)
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher

# --------------------------------------------------------------------------
# Helpers: scripted streamer that records the last user message it received.
# --------------------------------------------------------------------------


class _ScriptedReplyStreamer:
    """Returns a single canned assistant turn; records the user prompts seen."""

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
        """The plain text of the most recent ``user`` message in the last call."""
        last = self.calls[-1]
        user_msgs = [m for m in last if m.role == "user"]
        assert user_msgs, "no user message in last call"
        return _flatten(user_msgs[-1])


def _flatten(message: ConversationMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts)


def _harness_with_reply(reply: str) -> tuple[Harness, _ScriptedReplyStreamer]:
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


def _valid_reply(
    *,
    spec: str = "# Plan\n\nDo the thing.",
    steps: list[dict[str, object]] | None = None,
    evaluator_enabled: bool | None = None,
    ledger_extra: str = "",
) -> str:
    if steps is None:
        steps = [{"id": "s1", "description": "do thing one"}]
    ledger: dict[str, object] = {"steps": steps}
    if evaluator_enabled is not None:
        ledger["evaluator_enabled"] = evaluator_enabled
    body = json.dumps(ledger)
    return f"<spec>\n{spec}\n</spec>\n<ledger>\n{body}{ledger_extra}\n</ledger>"


# --------------------------------------------------------------------------
# Callable shape + happy-path parse
# --------------------------------------------------------------------------


def test_make_planner_head_returns_callable() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)
    assert callable(head)


async def test_planner_head_returns_planner_output() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert isinstance(out, PlannerOutput)


async def test_planner_head_extracts_spec_markdown() -> None:
    harness, _ = _harness_with_reply(
        _valid_reply(spec="# Heading\n\nSome prose.")
    )
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.spec_markdown == "# Heading\n\nSome prose."


async def test_planner_head_extracts_ledger_steps() -> None:
    harness, _ = _harness_with_reply(
        _valid_reply(
            steps=[
                {"id": "s1", "description": "first"},
                {
                    "id": "s2",
                    "description": "second",
                    "sprint_target": 2,
                    "notes": "watch out",
                },
            ]
        )
    )
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.steps == (
        LedgerStep(id="s1", description="first"),
        LedgerStep(
            id="s2",
            description="second",
            sprint_target=2,
            notes="watch out",
        ),
    )


async def test_planner_head_sets_task_id_and_intent_on_ledger() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    out = await head("task-007", "make it good")

    assert out.ledger.task_id == "task-007"
    assert out.ledger.intent == "make it good"


async def test_planner_head_stamps_created_at() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.created_at > 0.0


async def test_planner_head_defaults_evaluator_enabled_to_true() -> None:
    harness, _ = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.evaluator_enabled is True


async def test_planner_head_honours_task_level_evaluator_disabled() -> None:
    harness, _ = _harness_with_reply(_valid_reply(evaluator_enabled=False))
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.evaluator_enabled is False


# --------------------------------------------------------------------------
# Prompt construction: what the model actually sees
# --------------------------------------------------------------------------


async def test_planner_head_intent_includes_task_id_and_user_intent() -> None:
    harness, streamer = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    await head("task-xyz", "build a robot")

    prompt = streamer.last_user_text
    assert "task-xyz" in prompt
    assert "build a robot" in prompt


async def test_planner_head_intent_documents_required_envelope() -> None:
    """The instruction must name both tags so a real LLM has a chance."""
    harness, streamer = _harness_with_reply(_valid_reply())
    head = make_planner_head(harness)

    await head("task-001", "ship it")

    prompt = streamer.last_user_text
    assert "<spec>" in prompt
    assert "</spec>" in prompt
    assert "<ledger>" in prompt
    assert "</ledger>" in prompt


# --------------------------------------------------------------------------
# Parser tolerance: model wraps ledger JSON in a ```json fence
# --------------------------------------------------------------------------


async def test_planner_head_strips_inner_json_code_fence() -> None:
    steps = [{"id": "s1", "description": "first"}]
    body = json.dumps({"steps": steps})
    reply = (
        "<spec># Plan</spec>\n"
        f"<ledger>\n```json\n{body}\n```\n</ledger>"
    )
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.steps == (LedgerStep(id="s1", description="first"),)


async def test_planner_head_strips_bare_code_fence_without_lang() -> None:
    steps = [{"id": "s1", "description": "first"}]
    body = json.dumps({"steps": steps})
    reply = (
        "<spec># Plan</spec>\n"
        f"<ledger>\n```\n{body}\n```\n</ledger>"
    )
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.ledger.steps == (LedgerStep(id="s1", description="first"),)


async def test_planner_head_tolerates_surrounding_prose_in_reply() -> None:
    """The model often prefaces its answer with a sentence or two; ignore it."""
    body = json.dumps({"steps": [{"id": "s1", "description": "first"}]})
    reply = (
        "Sure, here is the plan you requested.\n\n"
        "<spec>\n# Plan\n\nbody.\n</spec>\n\n"
        f"<ledger>\n{body}\n</ledger>\n\n"
        "Let me know if you want changes."
    )
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert out.spec_markdown == "# Plan\n\nbody."
    assert out.ledger.steps[0].id == "s1"


# --------------------------------------------------------------------------
# Parse failures
# --------------------------------------------------------------------------


async def test_planner_head_raises_when_spec_tag_missing() -> None:
    body = json.dumps({"steps": [{"id": "s1", "description": "first"}]})
    reply = f"<ledger>{body}</ledger>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="spec"):
        await head("task-001", "ship it")


async def test_planner_head_raises_when_ledger_tag_missing() -> None:
    reply = "<spec># Plan</spec>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="ledger"):
        await head("task-001", "ship it")


async def test_planner_head_raises_on_invalid_ledger_json() -> None:
    reply = "<spec># Plan</spec>\n<ledger>{not json}</ledger>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="JSON"):
        await head("task-001", "ship it")


async def test_planner_head_raises_when_ledger_not_object() -> None:
    reply = "<spec># Plan</spec>\n<ledger>[1, 2, 3]</ledger>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="object"):
        await head("task-001", "ship it")


async def test_planner_head_raises_when_steps_missing() -> None:
    reply = '<spec># Plan</spec>\n<ledger>{"steps": []}</ledger>'
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="step"):
        await head("task-001", "ship it")


async def test_planner_head_raises_when_step_missing_id() -> None:
    body = json.dumps({"steps": [{"description": "no id"}]})
    reply = f"<spec># Plan</spec>\n<ledger>{body}</ledger>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="id"):
        await head("task-001", "ship it")


async def test_planner_head_raises_when_step_missing_description() -> None:
    body = json.dumps({"steps": [{"id": "s1"}]})
    reply = f"<spec># Plan</spec>\n<ledger>{body}</ledger>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="description"):
        await head("task-001", "ship it")


async def test_planner_head_raises_when_spec_body_is_empty() -> None:
    body = json.dumps({"steps": [{"id": "s1", "description": "first"}]})
    reply = f"<spec>   </spec>\n<ledger>{body}</ledger>"
    harness, _ = _harness_with_reply(reply)
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError, match="spec"):
        await head("task-001", "ship it")


# --------------------------------------------------------------------------
# Engine-level failures bubble unchanged
# --------------------------------------------------------------------------


class _ErrorStreamer:
    """One-turn streamer that errors before completing."""

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        from dream.engine._events import ErrorEvent

        yield ErrorEvent(message="upstream blew up", recoverable=False)
        yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())


async def test_planner_head_propagates_role_session_error() -> None:
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
    head = make_planner_head(harness)

    with pytest.raises(RoleSessionError):
        await head("task-001", "ship it")


# --------------------------------------------------------------------------
# Harness-dir overlay propagates through to ``run_role``
# --------------------------------------------------------------------------


async def test_planner_head_uses_harness_dir_for_role_overlay(
    tmp_path: Path,
) -> None:
    """An overlay manifest's ``system_prompt`` reaches the engine factory."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "planner.toml").write_text(
        'system_prompt = "OVERLAY PROMPT"\n', encoding="utf-8"
    )

    captured: list[SessionOptions] = []
    streamer = _ScriptedReplyStreamer(_valid_reply())

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        captured.append(options)
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    head = make_planner_head(harness, harness_dir=tmp_path)

    await head("task-001", "ship it")

    assert captured[0].system_prompt is not None
    assert captured[0].system_prompt.startswith("OVERLAY PROMPT")
