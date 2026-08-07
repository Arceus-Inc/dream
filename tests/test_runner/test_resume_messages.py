"""``resume_messages`` on role sessions and ``run_task`` (ledger / save-resume seam)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.engine._engine import QueryEngine
from dream.harness import Harness, HarnessConfig
from dream.messages import ConversationMessage, TextBlock
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _factory(streamer: FakeStreamer):
    def _make(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return _make


@pytest.mark.asyncio
async def test_run_role_seeds_resume_messages_into_provider_transcript() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["continued"])])
    harness = Harness(HarnessConfig(_engine_factory=_factory(streamer)))  # type: ignore[call-arg]
    prior = [
        ConversationMessage(role="user", content=[TextBlock(text="find the bug")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="I'll search")]),
    ]

    result = await harness.run_role(
        "generator",
        "keep going",
        resume_messages=prior,
    )

    assert result.final_text == "continued"
    assert streamer.calls, "expected at least one provider turn"
    roles_and_text = [(m.role, m.text) for m in streamer.calls[0]]
    assert ("user", "find the bug") in roles_and_text
    assert ("assistant", "I'll search") in roles_and_text
    assert ("user", "keep going") in roles_and_text
    assert roles_and_text.index(("user", "find the bug")) < roles_and_text.index(
        ("user", "keep going")
    )


@pytest.mark.asyncio
async def test_start_session_seeds_transcript_property() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    harness = Harness(HarnessConfig(_engine_factory=_factory(streamer)))  # type: ignore[call-arg]
    prior = [
        ConversationMessage(role="user", content=[TextBlock(text="hi")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="hello")]),
    ]

    session = await harness.start_session(resume_messages=prior)
    assert [(m.role, m.text) for m in session.transcript] == [
        ("user", "hi"),
        ("assistant", "hello"),
    ]
    await session.close()


@pytest.mark.asyncio
async def test_make_generator_head_forwards_resume_messages() -> None:
    from dream.planner import LedgerStep
    from dream.runner import make_generator_head

    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["did work"])])
    harness = Harness(HarnessConfig(_engine_factory=_factory(streamer)))  # type: ignore[call-arg]
    prior = [
        ConversationMessage(role="assistant", content=[TextBlock(text="earlier")]),
    ]
    head = make_generator_head(harness, resume_messages=prior)
    step = LedgerStep(
        id="s1",
        description="touch a file",
        status="pending",
    )
    await head("t1", 1, None, step)
    assert any(m.text == "earlier" for m in streamer.calls[0])


def test_resolve_heads_forwards_resume_messages_to_generator_factory() -> None:
    import dream.runner as runner_mod
    from dream.harness import Harness, HarnessConfig

    seen: dict[str, object] = {}

    def _fake_make_generator_head(harness: object, **kwargs: object) -> object:
        seen.update(kwargs)

        async def _gen(*_a: object, **_k: object) -> None:
            return None

        return _gen

    def _stub_head(*_a: object, **_k: object) -> object:
        async def _fn(*_args: object, **_kwargs: object) -> None:
            return None

        return _fn

    originals = {
        "make_generator_head": runner_mod.make_generator_head,
        "make_planner_head": runner_mod.make_planner_head,
        "make_evaluator_propose_head": runner_mod.make_evaluator_propose_head,
        "make_generator_respond_head": runner_mod.make_generator_respond_head,
        "make_evaluator_head": runner_mod.make_evaluator_head,
    }
    runner_mod.make_generator_head = _fake_make_generator_head  # type: ignore[assignment]
    runner_mod.make_planner_head = _stub_head  # type: ignore[assignment]
    runner_mod.make_evaluator_propose_head = _stub_head  # type: ignore[assignment]
    runner_mod.make_generator_respond_head = _stub_head  # type: ignore[assignment]
    runner_mod.make_evaluator_head = _stub_head  # type: ignore[assignment]
    try:
        h = Harness(HarnessConfig())
        prior = [
            ConversationMessage(role="user", content=[TextBlock(text="prior")]),
        ]
        h._resolve_heads(
            planner=None,
            generator_execute=None,
            evaluator_propose=None,
            generator_respond=None,
            evaluator_run=None,
            intent="go",
            harness_dir=None,
            observer=None,
            resume_messages=prior,
        )
    finally:
        for name, original in originals.items():
            setattr(runner_mod, name, original)

    assert seen.get("resume_messages") is prior
