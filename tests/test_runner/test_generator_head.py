"""Spec 10 slice G4 — production generator head.

``make_generator_head(harness, ...)`` returns a :data:`GeneratorExecute`
that opens a ``generator``-bound session through ``Harness.run_role`` and
lets the model do the actual work in the worktree.

Unlike the planner head (G3), the generator emits **no parsed artefact**
— its side effects are file writes (tools), and the engine layer owns
event capture. The head's only job is to assemble a complete prompt
from the sprint contract + step and forward it.

What this slice pins:

- Callable shape matches ``GeneratorExecute``
  (``(task_id, sprint_n, contract|None, step) -> Awaitable[None]``) so
  ``run_task`` accepts it without adaptation.
- The intent always carries task id + sprint number + the step's id
  and description.
- When a contract is present, the intent includes goal, acceptance
  criteria, the DoD REVIEW RUBRIC (when set), verification steps, and
  scope guards so the model can self-check before stopping against the
  same bar the evaluator will judge.
- When the contract is ``None`` (evaluator disabled), the intent
  documents the step alone — no fake contract is fabricated.
- ``RoleSessionError`` from the session layer propagates unchanged.
"""

from __future__ import annotations

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
from dream.planner import LedgerStep
from dream.runner import RoleSessionError, make_generator_head
from dream.session import SessionOptions
from dream.sprint import SprintContract
from tests.test_engine._fakes import FakeDispatcher

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class _ScriptedReplyStreamer:
    """Yields one scripted assistant turn; records the user prompts seen."""

    def __init__(self, reply: str = "done") -> None:
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
        return "".join(
            b.text for b in user_msgs[-1].content if isinstance(b, TextBlock)
        )


def _harness_with_reply(
    reply: str = "done",
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


def _harness_capturing_options() -> (
    tuple[Harness, _ScriptedReplyStreamer, list[SessionOptions]]
):
    captured: list[SessionOptions] = []
    streamer = _ScriptedReplyStreamer()

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
        "widget passes accessibility audit",
    ),
    verification_steps: tuple[dict[str, str], ...] = (
        {"kind": "test", "command": "pytest tests/widget"},
        {"kind": "lint", "command": "ruff check src/widget"},
    ),
    scope_includes: tuple[str, ...] = ("src/widget/", "tests/widget/"),
    scope_excludes: tuple[str, ...] = ("src/legacy/",),
    evaluator_enabled: bool = True,
    rubric: str = "",
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
        rubric=rubric,
    )


def _step(
    *, step_id: str = "s1", description: str = "build the widget shell"
) -> LedgerStep:
    return LedgerStep(id=step_id, description=description)


# --------------------------------------------------------------------------
# Callable shape + return value
# --------------------------------------------------------------------------


def test_make_generator_head_returns_callable() -> None:
    harness, _ = _harness_with_reply()
    head = make_generator_head(harness)
    assert callable(head)


async def test_generator_head_returns_none() -> None:
    harness, _ = _harness_with_reply()
    head = make_generator_head(harness)

    out = await head("task-001", 1, _contract(), _step())

    assert out is None


async def test_generator_head_carries_transcript_between_sprints() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head("task-001", 1, _contract(sprint_number=1), _step())
    await head("task-001", 2, _contract(sprint_number=2), _step(step_id="s2"))

    assert len(streamer.calls) == 2
    second_messages = streamer.calls[1]
    second_user_text = [
        block.text
        for message in second_messages
        if message.role == "user"
        for block in message.content
        if isinstance(block, TextBlock)
    ]
    assert any("sprint 1" in text for text in second_user_text)
    assert any("sprint 2" in text for text in second_user_text)


# --------------------------------------------------------------------------
# Role wiring: invokes ``run_role`` with ``generator``
# --------------------------------------------------------------------------


async def test_generator_head_uses_generator_role() -> None:
    harness, _, captured = _harness_capturing_options()
    head = make_generator_head(harness)

    await head("task-001", 1, _contract(), _step())

    # The harness ran exactly one session; its options metadata is what
    # ``Harness.run_role("generator", …)`` stamps in.
    assert len(captured) == 1
    assert captured[0].metadata.get("dream.role") == "generator"


# --------------------------------------------------------------------------
# Intent assembly: always-present pieces (task id, sprint, step)
# --------------------------------------------------------------------------


async def test_generator_head_intent_includes_task_id_and_sprint_number() -> (
    None
):
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head("task-xyz", 7, _contract(task_id="task-xyz", sprint_number=7), _step())

    prompt = streamer.last_user_text
    assert "task-xyz" in prompt
    assert "7" in prompt  # sprint number surfaces somewhere


async def test_generator_head_intent_includes_step_id_and_description() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    step = _step(step_id="alpha-1", description="wire the kettle to the mains")
    await head("task-001", 1, _contract(), step)

    prompt = streamer.last_user_text
    assert "alpha-1" in prompt
    assert "wire the kettle to the mains" in prompt


async def test_generator_head_intent_includes_step_notes_when_present() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    step = LedgerStep(
        id="s1",
        description="do thing",
        notes="watch the off-by-one in row calc",
    )
    await head("task-001", 1, _contract(), step)

    assert "watch the off-by-one" in streamer.last_user_text


async def test_generator_head_intent_omits_notes_section_when_blank() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head("task-001", 1, _contract(), _step())  # notes defaults to ""

    # The "NOTES" header should only appear when step.notes has content.
    assert "NOTES" not in streamer.last_user_text


# --------------------------------------------------------------------------
# Intent assembly: contract-present pieces
# --------------------------------------------------------------------------


async def test_generator_head_intent_includes_goal_from_contract() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head(
        "task-001", 1, _contract(goal="render the dashboard"), _step()
    )

    assert "render the dashboard" in streamer.last_user_text


async def test_generator_head_intent_includes_all_acceptance_criteria() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head(
        "task-001",
        1,
        _contract(
            acceptance_criteria=(
                "renders under 50ms",
                "no axe violations",
                "covered by unit test",
            )
        ),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "renders under 50ms" in prompt
    assert "no axe violations" in prompt
    assert "covered by unit test" in prompt


async def test_generator_head_embeds_task_intent_as_source_of_truth() -> None:
    """Contract ACs can dilute; the original task Intent must stay in the
    generator prompt as the non-negotiable work contract (first principle:
    implement against the real Intent, not a softened rewrite).
    """
    harness, streamer = _harness_with_reply()
    head = make_generator_head(
        harness,
        task_intent=(
            "Publish a launch brief covering audience, offer, and one CTA; "
            "do not invent extra channels."
        ),
    )

    await head(
        "task-001",
        1,
        _contract(
            acceptance_criteria=(
                "MUST write a brief",  # diluted
            )
        ),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "audience, offer, and one CTA" in prompt
    assert "TASK INTENT" in prompt
    # Coaching lives in standing orders; user turn is data-only.
    assert "source of truth" not in prompt.lower()
    assert "must not weaken" not in prompt.lower()


async def test_generator_head_intent_includes_review_rubric_when_set() -> None:
    """DoD rubric must reach the generator — same bar the evaluator judges."""
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)
    rubric = (
        "PASS only if token_store.py exists as its own module with "
        "create(subject, ttl_s, scopes) -> str."
    )

    await head(
        "task-001",
        1,
        _contract(rubric=rubric),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "REVIEW RUBRIC" in prompt
    assert "token_store.py exists as its own module" in prompt
    assert "create(subject, ttl_s, scopes) -> str" in prompt


async def test_generator_head_intent_omits_review_rubric_when_blank() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head("task-001", 1, _contract(rubric=""), _step())

    assert "REVIEW RUBRIC" not in streamer.last_user_text


async def test_generator_head_intent_includes_verification_steps() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head(
        "task-001",
        1,
        _contract(
            verification_steps=(
                {"kind": "test", "command": "pytest tests/foo"},
                {"kind": "lint", "command": "ruff check ."},
            )
        ),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "pytest tests/foo" in prompt
    assert "ruff check ." in prompt
    assert "test" in prompt
    assert "lint" in prompt


async def test_generator_head_intent_includes_scope_guards() -> None:
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head(
        "task-001",
        1,
        _contract(
            scope_includes=("src/widget/", "tests/widget/"),
            scope_excludes=("src/legacy/", "vendor/"),
        ),
        _step(),
    )

    prompt = streamer.last_user_text
    assert "src/widget/" in prompt
    assert "tests/widget/" in prompt
    assert "src/legacy/" in prompt
    assert "vendor/" in prompt


async def test_generator_head_intent_omits_scope_sections_when_empty() -> None:
    """An empty scope list shouldn't render a dangling header."""
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head(
        "task-001",
        1,
        _contract(scope_includes=(), scope_excludes=()),
        _step(),
    )

    prompt = streamer.last_user_text
    # Lower-case 'include'/'exclude' may appear in step description; only the
    # ALL-CAPS section headers should be suppressed.
    assert "SCOPE INCLUDES" not in prompt
    assert "SCOPE EXCLUDES" not in prompt


# --------------------------------------------------------------------------
# Evaluator-disabled branch: contract is None
# --------------------------------------------------------------------------


async def test_generator_head_works_when_contract_is_none() -> None:
    harness, _ = _harness_with_reply()
    head = make_generator_head(harness)

    # Must not raise — evaluator-disabled tasks pass contract=None.
    out = await head("task-001", 1, None, _step())

    assert out is None


async def test_generator_head_intent_omits_contract_sections_when_none() -> (
    None
):
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head(
        "task-001", 1, None, _step(description="do the thing")
    )

    prompt = streamer.last_user_text
    # The step is still present.
    assert "do the thing" in prompt
    # But contract-specific section headers are not.
    assert "ACCEPTANCE CRITERIA" not in prompt
    assert "VERIFICATION STEPS" not in prompt
    assert "GOAL" not in prompt


async def test_generator_head_intent_signals_evaluator_disabled_when_no_contract() -> (
    None
):
    """The prompt should tell the model the verifier won't double-check it."""
    harness, streamer = _harness_with_reply()
    head = make_generator_head(harness)

    await head("task-001", 1, None, _step())

    # We don't pin exact wording, just that the prompt acknowledges the
    # absence so the model self-checks instead of leaning on the evaluator.
    assert "evaluator" in streamer.last_user_text.lower()


# --------------------------------------------------------------------------
# harness_dir overlay forwarding
# --------------------------------------------------------------------------


async def test_generator_head_uses_harness_dir_for_role_overlay(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "generator.toml").write_text(
        'system_prompt = "OVERLAY GEN PROMPT"\n', encoding="utf-8"
    )

    harness, _, captured = _harness_capturing_options()
    head = make_generator_head(harness, harness_dir=tmp_path)

    await head("task-001", 1, _contract(), _step())

    assert captured[0].system_prompt is not None
    assert captured[0].system_prompt.startswith("OVERLAY GEN PROMPT")


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


async def test_generator_head_propagates_role_session_error() -> None:
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
    head = make_generator_head(harness)

    with pytest.raises(RoleSessionError):
        await head("task-001", 1, _contract(), _step())
