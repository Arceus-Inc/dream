"""Oracle execution — the evaluator judges evidence, not vibes (spec 15 P3 §1).

``run_oracle`` executes the contract's ``verification_steps`` as real
subprocesses; the evaluator head injects the structured results into the
verdict prompt and refuses to honour a model ``pass`` when the oracle is
red — ``pass`` requires the oracle green when verification steps exist.
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
from dream.planner import LedgerStep
from dream.runner import make_evaluator_head
from dream.runner._oracle import run_oracle
from dream.session import SessionOptions
from dream.sprint import SprintContract
from tests.test_engine._fakes import FakeDispatcher

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class _ScriptedReplyStreamer:
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
        assert user_msgs
        return "".join(
            b.text for b in user_msgs[-1].content if isinstance(b, TextBlock)
        )


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

    return Harness(HarnessConfig(_engine_factory=_factory)), streamer


def _contract(
    verification_steps: tuple[dict[str, str], ...],
) -> SprintContract:
    return SprintContract(
        task_id="task-001",
        sprint_number=1,
        goal="ship it",
        acceptance_criteria=("it works",),
        verification_steps=verification_steps,
        scope_includes=(),
        scope_excludes=(),
        evaluator_enabled=True,
    )


def _step() -> LedgerStep:
    return LedgerStep(id="s1", description="do the thing", status="in_progress")


def _pass_verdict() -> str:
    return (
        "<verdict>"
        + json.dumps(
            {"outcome": "pass", "score": 1.0, "notes": "", "items": []}
        )
        + "</verdict>"
    )


# --------------------------------------------------------------------------
# run_oracle
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_steps_means_no_oracle(tmp_path: Path) -> None:
    result = await run_oracle(_contract(()), cwd=tmp_path)
    assert result is None


@pytest.mark.asyncio
async def test_steps_without_commands_mean_no_oracle(tmp_path: Path) -> None:
    result = await run_oracle(_contract(({"kind": "test"},)), cwd=tmp_path)
    assert result is None


@pytest.mark.asyncio
async def test_green_oracle(tmp_path: Path) -> None:
    result = await run_oracle(
        _contract(({"kind": "test", "command": "echo all good"},)), cwd=tmp_path
    )
    assert result is not None
    assert result.green
    assert "all good" not in result.failure_items()  # no failures at all
    assert "[success]" in result.render_block()


@pytest.mark.asyncio
async def test_red_oracle_carries_failures(tmp_path: Path) -> None:
    result = await run_oracle(
        _contract(
            (
                {"kind": "test", "command": "echo ok"},
                {"kind": "lint", "command": "echo broken >&2; exit 3"},
            )
        ),
        cwd=tmp_path,
    )
    assert result is not None
    assert not result.green
    items = result.failure_items()
    assert len(items) == 1
    assert "lint" in items[0]
    block = result.render_block()
    assert "[failed]" in block
    assert "broken" in block


# --------------------------------------------------------------------------
# Evaluator head — no oracle sidecar (verify-in-session)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluator_head_does_not_run_oracle_or_downgrade(
    tmp_path: Path,
) -> None:
    """Failing verification_steps must NOT be executed by a sidecar or force needs-changes.

    The evaluator session runs them via bash; the head only asks for a verdict.
    """
    harness, streamer = _harness_with_reply(_pass_verdict())
    head = make_evaluator_head(harness, worktree_root=tmp_path)
    record = await head(
        "task-001",
        1,
        _contract(({"kind": "test", "command": "exit 1"},)),
        _step(),
    )
    assert record.outcome == "pass"  # model verdict stands; no oracle override
    assert "ORACLE RESULTS" not in streamer.last_user_text
    assert "oracle" not in (record.notes or "").lower()


@pytest.mark.asyncio
async def test_verification_steps_listed_for_evaluator_to_run(tmp_path: Path) -> None:
    harness, streamer = _harness_with_reply(_pass_verdict())
    head = make_evaluator_head(harness, worktree_root=tmp_path)
    await head(
        "task-001",
        1,
        _contract(({"kind": "test", "command": "echo fine"},)),
        _step(),
    )
    assert "echo fine" in streamer.last_user_text
    assert "ORACLE RESULTS" not in streamer.last_user_text
