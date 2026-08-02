"""Spec 10-H — LLM-backed negotiator heads.

Two factories build callables compatible with
:data:`dream.sprint.EvaluatorPropose` and
:data:`dream.sprint.GeneratorRespond` by opening one
:meth:`Harness.run_role` session per negotiation round and parsing a
strict XML-style envelope from the model's reply:

- ``make_evaluator_propose_head(harness, *, harness_dir=None)`` →
  evaluator opens the negotiation by proposing acceptance criteria.
  Envelope: ``<proposal>{JSON list of strings}</proposal>``.

- ``make_generator_respond_head(harness, *, harness_dir=None)`` →
  generator either accepts or counters with a different list.
  Envelope:
  ``<response>{"accept": bool, "counter": [...]|null}</response>``.

Both heads:

- Tolerate a ```json fence inside the envelope and prose around it.
- Surface parse failures as dedicated ``...HeadParseError`` exceptions
  so the runner can distinguish them from
  :class:`dream.runner.RoleSessionError`.
- Open under the right role (``evaluator`` / ``generator``) so the
  per-task role lock and role-aware tool gating apply automatically.
- Embed the running negotiation log in every prompt so each turn is
  context-aware without the runner replaying the whole exchange.

They are exercised by :func:`dream.sprint.negotiate_contract_async`
inside :func:`dream.runner.run_task`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

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
from dream.runner import (
    EvaluatorProposeHeadParseError,
    GeneratorRespondHeadParseError,
    RoleSessionError,
    make_evaluator_propose_head,
    make_generator_respond_head,
)
from dream.session import SessionOptions
from dream.sprint import NegotiationEntry, negotiate_contract_async
from tests.test_engine._fakes import FakeDispatcher

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class _ScriptedReplyStreamer:
    """Yields one scripted assistant turn per call; records the prompts seen."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("scripted streamer ran out of replies")
        reply = self._replies.pop(0)
        yield AssistantTextDelta(text=reply)
        yield AssistantTurnComplete(
            blocks=[TextBlock(text=reply)],
            usage=UsageSnapshot(),
        )

    @property
    def last_user_text(self) -> str:
        last = self.calls[-1]
        user_msgs = [m for m in last if m.role == "user"]
        assert user_msgs, "no user message in last call"
        return "".join(b.text for b in user_msgs[-1].content if isinstance(b, TextBlock))


class _ErrorStreamer:
    """Emits an ErrorEvent so the role-session layer raises RoleSessionError."""

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        from dream.engine._events import ErrorEvent

        yield ErrorEvent(message="boom", recoverable=False)
        yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())


def _proposal_envelope(
    items: list[str],
    *,
    fence: bool = False,
    prose_before: str = "",
    prose_after: str = "",
    tag: str = "proposal",
) -> str:
    inner = json.dumps(items)
    if fence:
        inner = f"```json\n{inner}\n```"
    return f"{prose_before}<{tag}>{inner}</{tag}>{prose_after}"


def _response_envelope(
    *,
    accept: bool,
    counter: list[str] | None = None,
    fence: bool = False,
    prose_before: str = "",
    prose_after: str = "",
    tag: str = "response",
) -> str:
    body: dict[str, Any] = {"accept": accept, "counter": counter}
    inner = json.dumps(body)
    if fence:
        inner = f"```json\n{inner}\n```"
    return f"{prose_before}<{tag}>{inner}</{tag}>{prose_after}"


def _harness_with_replies(
    replies: list[str],
) -> tuple[Harness, _ScriptedReplyStreamer]:
    streamer = _ScriptedReplyStreamer(replies)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return Harness(HarnessConfig(_engine_factory=_factory)), streamer  # type: ignore[call-arg]


def _harness_capturing_options(
    replies: list[str],
) -> tuple[Harness, _ScriptedReplyStreamer, list[SessionOptions]]:
    captured: list[SessionOptions] = []
    streamer = _ScriptedReplyStreamer(replies)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        captured.append(options)
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return (
        Harness(HarnessConfig(_engine_factory=_factory)),  # type: ignore[call-arg]
        streamer,
        captured,
    )


def _harness_raising() -> Harness:
    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=_ErrorStreamer(),
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# make_evaluator_propose_head — shape + parsing
# --------------------------------------------------------------------------


def test_make_evaluator_propose_head_returns_callable() -> None:
    harness, _ = _harness_with_replies([_proposal_envelope(["x"])])
    head = make_evaluator_propose_head(harness)
    assert callable(head)


async def test_evaluator_propose_head_returns_list_of_strings() -> None:
    harness, _ = _harness_with_replies([_proposal_envelope(["MUST render", "SHOULD pass axe"])])
    head = make_evaluator_propose_head(harness)

    result = head(1, [])
    # may return list or awaitable; the head is async-shaped
    if hasattr(result, "__await__"):
        result = await result  # type: ignore[assignment]

    assert result == ["MUST render", "SHOULD pass axe"]


async def test_evaluator_propose_head_tolerates_prose_around_envelope() -> None:
    harness, _ = _harness_with_replies(
        [
            _proposal_envelope(
                ["c1", "c2"],
                prose_before="Here are my proposed criteria:\n\n",
                prose_after="\n\nLet me know.",
            )
        ]
    )
    head = make_evaluator_propose_head(harness)

    out = await head(1, [])  # type: ignore[misc]
    assert out == ["c1", "c2"]


async def test_evaluator_propose_head_tolerates_json_fence() -> None:
    harness, _ = _harness_with_replies([_proposal_envelope(["c"], fence=True)])
    head = make_evaluator_propose_head(harness)

    out = await head(1, [])  # type: ignore[misc]
    assert out == ["c"]


async def test_evaluator_propose_head_tag_is_case_insensitive() -> None:
    harness, _ = _harness_with_replies([_proposal_envelope(["c"], tag="PROPOSAL")])
    head = make_evaluator_propose_head(harness)

    out = await head(1, [])  # type: ignore[misc]
    assert out == ["c"]


async def test_evaluator_propose_head_missing_envelope_raises() -> None:
    harness, _ = _harness_with_replies(["I think we need clarity, hmm."])
    head = make_evaluator_propose_head(harness)

    with pytest.raises(EvaluatorProposeHeadParseError):
        await head(1, [])  # type: ignore[misc]


async def test_evaluator_propose_head_invalid_json_raises() -> None:
    harness, _ = _harness_with_replies(["<proposal>not-json</proposal>"])
    head = make_evaluator_propose_head(harness)

    with pytest.raises(EvaluatorProposeHeadParseError):
        await head(1, [])  # type: ignore[misc]


async def test_evaluator_propose_head_non_list_payload_raises() -> None:
    harness, _ = _harness_with_replies(['<proposal>{"x":1}</proposal>'])
    head = make_evaluator_propose_head(harness)

    with pytest.raises(EvaluatorProposeHeadParseError):
        await head(1, [])  # type: ignore[misc]


async def test_evaluator_propose_head_non_string_item_raises() -> None:
    harness, _ = _harness_with_replies(["<proposal>[1, 2]</proposal>"])
    head = make_evaluator_propose_head(harness)

    with pytest.raises(EvaluatorProposeHeadParseError):
        await head(1, [])  # type: ignore[misc]


async def test_evaluator_propose_head_propagates_engine_errors() -> None:
    harness = _harness_raising()
    head = make_evaluator_propose_head(harness)

    with pytest.raises(RoleSessionError):
        await head(1, [])  # type: ignore[misc]


# --------------------------------------------------------------------------
# make_evaluator_propose_head — role + log wiring
# --------------------------------------------------------------------------


async def test_evaluator_propose_head_uses_evaluator_role() -> None:
    harness, _, captured = _harness_capturing_options([_proposal_envelope(["c"])])
    head = make_evaluator_propose_head(harness)

    await head(1, [])  # type: ignore[misc]

    assert len(captured) == 1
    assert captured[0].metadata.get("dream.role") == "evaluator"


async def test_evaluator_propose_head_embeds_negotiation_log() -> None:
    harness, streamer = _harness_with_replies([_proposal_envelope(["c2"])])
    head = make_evaluator_propose_head(harness)

    log: list[NegotiationEntry] = [
        NegotiationEntry(
            ts="2025-01-01T00:00:00Z",
            from_role="carry",
            to_role="evaluator",
            message="carry-over from prior sprint: add aria-label to button",
        ),
        NegotiationEntry(
            ts="2025-01-01T00:00:01Z",
            from_role="evaluator",
            to_role="generator",
            message="propose r1: ['c1']",
        ),
        NegotiationEntry(
            ts="2025-01-01T00:00:02Z",
            from_role="generator",
            to_role="evaluator",
            message="counter r1: ['c1-alt']",
        ),
    ]

    await head(2, log)  # type: ignore[misc]

    prompt = streamer.last_user_text
    assert "add aria-label to button" in prompt
    assert "propose r1" in prompt
    assert "counter r1" in prompt
    # the round number must be wired into the prompt so the model knows
    # where it is. Assert the exact round phrase, not a bare "2" — the log
    # already contains "2025-..." timestamps, so a bare digit check would
    # pass even if round wiring were broken.
    assert "negotiation round 2" in prompt


# --------------------------------------------------------------------------
# make_generator_respond_head — shape + parsing
# --------------------------------------------------------------------------


def test_make_generator_respond_head_returns_callable() -> None:
    harness, _ = _harness_with_replies([_response_envelope(accept=True)])
    head = make_generator_respond_head(harness)
    assert callable(head)


async def test_generator_respond_head_returns_accept_true_none_counter() -> None:
    harness, _ = _harness_with_replies([_response_envelope(accept=True)])
    head = make_generator_respond_head(harness)

    accept, counter = await head(1, [], ["c"])  # type: ignore[misc]
    assert accept is True
    assert counter is None


async def test_generator_respond_head_returns_accept_false_with_counter() -> None:
    harness, _ = _harness_with_replies([_response_envelope(accept=False, counter=["c-alt"])])
    head = make_generator_respond_head(harness)

    accept, counter = await head(1, [], ["c"])  # type: ignore[misc]
    assert accept is False
    assert counter == ["c-alt"]


async def test_generator_respond_head_accept_with_explicit_null_counter() -> None:
    """Accepting MUST imply counter is None even if model omits it."""
    harness, _ = _harness_with_replies(['<response>{"accept": true}</response>'])
    head = make_generator_respond_head(harness)

    accept, counter = await head(1, [], ["c"])  # type: ignore[misc]
    assert accept is True
    assert counter is None


async def test_generator_respond_head_tolerates_prose_around_envelope() -> None:
    harness, _ = _harness_with_replies(
        [
            _response_envelope(
                accept=False,
                counter=["c-new"],
                prose_before="After review:\n\n",
                prose_after="\n\nHappy to discuss.",
            )
        ]
    )
    head = make_generator_respond_head(harness)

    accept, counter = await head(1, [], ["c"])  # type: ignore[misc]
    assert accept is False
    assert counter == ["c-new"]


async def test_generator_respond_head_tolerates_json_fence() -> None:
    harness, _ = _harness_with_replies([_response_envelope(accept=True, fence=True)])
    head = make_generator_respond_head(harness)

    accept, counter = await head(1, [], ["c"])  # type: ignore[misc]
    assert accept is True
    assert counter is None


async def test_generator_respond_head_tag_is_case_insensitive() -> None:
    harness, _ = _harness_with_replies([_response_envelope(accept=True, tag="RESPONSE")])
    head = make_generator_respond_head(harness)

    accept, _counter = await head(1, [], ["c"])  # type: ignore[misc]
    assert accept is True


async def test_generator_respond_head_missing_envelope_raises() -> None:
    harness, _ = _harness_with_replies(["Sure, that all sounds good."])
    head = make_generator_respond_head(harness)

    with pytest.raises(GeneratorRespondHeadParseError):
        await head(1, [], ["c"])  # type: ignore[misc]


async def test_generator_respond_head_invalid_json_raises() -> None:
    harness, _ = _harness_with_replies(["<response>not-json</response>"])
    head = make_generator_respond_head(harness)

    with pytest.raises(GeneratorRespondHeadParseError):
        await head(1, [], ["c"])  # type: ignore[misc]


async def test_generator_respond_head_missing_accept_raises() -> None:
    harness, _ = _harness_with_replies(['<response>{"counter": ["c"]}</response>'])
    head = make_generator_respond_head(harness)

    with pytest.raises(GeneratorRespondHeadParseError):
        await head(1, [], ["c"])  # type: ignore[misc]


async def test_generator_respond_head_non_bool_accept_raises() -> None:
    harness, _ = _harness_with_replies(['<response>{"accept": "yes"}</response>'])
    head = make_generator_respond_head(harness)

    with pytest.raises(GeneratorRespondHeadParseError):
        await head(1, [], ["c"])  # type: ignore[misc]


async def test_generator_respond_head_non_string_counter_item_raises() -> None:
    harness, _ = _harness_with_replies(
        ['<response>{"accept": false, "counter": [1, 2]}</response>']
    )
    head = make_generator_respond_head(harness)

    with pytest.raises(GeneratorRespondHeadParseError):
        await head(1, [], ["c"])  # type: ignore[misc]


async def test_generator_respond_head_propagates_engine_errors() -> None:
    harness = _harness_raising()
    head = make_generator_respond_head(harness)

    with pytest.raises(RoleSessionError):
        await head(1, [], ["c"])  # type: ignore[misc]


# --------------------------------------------------------------------------
# make_generator_respond_head — role + log wiring
# --------------------------------------------------------------------------


async def test_generator_respond_head_uses_generator_role() -> None:
    harness, _, captured = _harness_capturing_options([_response_envelope(accept=True)])
    head = make_generator_respond_head(harness)

    await head(1, [], ["c"])  # type: ignore[misc]

    assert len(captured) == 1
    assert captured[0].metadata.get("dream.role") == "generator"


async def test_generator_respond_head_embeds_proposal_and_log() -> None:
    harness, streamer = _harness_with_replies([_response_envelope(accept=True)])
    head = make_generator_respond_head(harness)

    log: list[NegotiationEntry] = [
        NegotiationEntry(
            ts="2025-01-01T00:00:00Z",
            from_role="evaluator",
            to_role="generator",
            message="propose r1: ['MUST render']",
        ),
    ]
    proposal = ["MUST render", "SHOULD pass axe"]

    await head(1, log, proposal)  # type: ignore[misc]

    prompt = streamer.last_user_text
    # The exact criteria up for review must appear in the prompt.
    assert "MUST render" in prompt
    assert "SHOULD pass axe" in prompt
    # And the prior negotiation log so the model sees the back-and-forth.
    assert "propose r1" in prompt


# --------------------------------------------------------------------------
# End-to-end: the two heads negotiate through negotiate_contract_async.
# --------------------------------------------------------------------------


async def test_heads_negotiate_to_acceptance_in_one_round() -> None:
    eval_harness, _ = _harness_with_replies([_proposal_envelope(["MUST render"])])
    gen_harness, _ = _harness_with_replies([_response_envelope(accept=True)])
    propose = make_evaluator_propose_head(eval_harness)
    respond = make_generator_respond_head(gen_harness)

    result = await negotiate_contract_async(
        evaluator_propose=propose,
        generator_respond=respond,
    )

    assert result.criteria == ("MUST render",)
    assert result.imposed is False
    assert result.rounds == 1


async def test_heads_negotiate_to_imposed_after_cap() -> None:
    # 3 rounds, evaluator always proposes "x", generator always counters.
    eval_harness, _ = _harness_with_replies([_proposal_envelope(["x"]) for _ in range(3)])
    gen_harness, _ = _harness_with_replies(
        [_response_envelope(accept=False, counter=["y"]) for _ in range(3)]
    )
    propose = make_evaluator_propose_head(eval_harness)
    respond = make_generator_respond_head(gen_harness)

    result = await negotiate_contract_async(
        evaluator_propose=propose,
        generator_respond=respond,
        max_rounds=3,
    )

    assert result.imposed is True
    assert result.rounds == 3
    # Evaluator's final proposal wins on imposition.
    assert result.criteria == ("x",)
    assert result.warning_event is not None
    assert result.warning_event["type"] == "sprint.negotiation_imposed"


# --------------------------------------------------------------------------
# Verifiable, task-specific acceptance criteria (spurious needs-changes fix)
# --------------------------------------------------------------------------

from dream.runner._negotiator_heads import (  # noqa: E402
    EVALUATOR_PROPOSE_INSTRUCTION_TEMPLATE,
)


async def test_propose_head_embeds_task_intent_so_criteria_are_specific() -> None:
    # Without the task intent the evaluator proposes generic boilerplate
    # ("MUST preserve backward compatibility", "MUST pass the existing suite")
    # that has nothing to do with the actual sprint. Thread the intent in.
    harness, streamer = _harness_with_replies([_proposal_envelope(["MUST x"])])
    propose = make_evaluator_propose_head(
        harness, intent="Create hello.py exposing greet(name) returning a greeting"
    )
    await propose(1, [])
    prompt = streamer.last_user_text
    assert "greet(name)" in prompt


async def test_propose_head_forbids_unstated_product_requirements() -> None:
    harness, streamer = _harness_with_replies([_proposal_envelope(["MUST x"])])
    propose = make_evaluator_propose_head(
        harness,
        intent="Persist nullable referrers and report top-referrer counts",
    )

    await propose(1, [])

    prompt = streamer.last_user_text.lower()
    assert "must not add" in prompt
    assert "unstated product behavior" in prompt


async def test_generator_respond_head_counters_scope_widening_against_intent() -> None:
    harness, streamer = _harness_with_replies([_response_envelope(accept=True)])
    respond = make_generator_respond_head(
        harness,
        intent="Persist nullable referrers and report top-referrer counts",
    )

    await respond(1, [], ["MUST impose a maximum referrer length"])

    prompt = streamer.last_user_text.lower()
    assert "persist nullable referrers" in prompt
    assert "counter" in prompt
    assert "widen" in prompt


def test_propose_template_restricts_criteria_to_worktree_verifiable() -> None:
    # The criteria the evaluator proposes must be checkable from the files in
    # the worktree; it must not demand documentation/changelog/git-history
    # evidence that the worktree flow never produces (root cause of the
    # endless needs-changes loop).
    t = EVALUATOR_PROPOSE_INSTRUCTION_TEMPLATE.lower()
    assert "verifiab" in t
    assert "documentation" in t
    assert "git history" in t or "commit history" in t


def test_propose_template_forbids_weakening_intent_requirements() -> None:
    """Short ACs must not drop concrete obligations named in the Intent."""
    t = EVALUATOR_PROPOSE_INSTRUCTION_TEMPLATE.lower()
    assert "weaken" in t or "omit" in t
    assert "intent" in t


def test_respond_template_counters_weakening_as_well_as_widening() -> None:
    from dream.runner._negotiator_heads import GENERATOR_RESPOND_INSTRUCTION_TEMPLATE

    t = GENERATOR_RESPOND_INSTRUCTION_TEMPLATE.lower()
    assert "weaken" in t or "omit" in t or "narrower" in t
    assert "widen" in t
