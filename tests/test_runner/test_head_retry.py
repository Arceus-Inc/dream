"""Tests for _head_retry.ask_until_parsed — Fix 1 (self-healing heads).

TDD: these tests are written BEFORE the implementation. They describe the
contract of ``ask_until_parsed`` and the per-head wiring through it.

Test groups:
1. Core helper contract — ask_until_parsed in isolation.
2. Planner head via ask_until_parsed — bad-then-good + exhaustion.
3. Evaluator head via ask_until_parsed — lightweight mirrors.
4. Observer event emission on retry.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
    EvaluatorHeadParseError,
    PlannerHeadParseError,
    RoleSessionError,
    make_evaluator_head,
    make_planner_head,
)
from dream.runner.events import HeadRetry
from dream.runner.observe import CapturingObserver
from dream.session import SessionOptions
from dream.sprint import EvaluationRecord, SprintContract
from tests.test_engine._fakes import FakeDispatcher

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _MultiReplyStreamer:
    """Returns scripted replies in order; records each call."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError(
                "_MultiReplyStreamer ran out of replies — test scripted too few"
            )
        reply = self._replies.pop(0)
        yield AssistantTextDelta(text=reply)
        yield AssistantTurnComplete(
            blocks=[TextBlock(text=reply)],
            usage=UsageSnapshot(),
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_user_text(self) -> str:
        last = self.calls[-1]
        user_msgs = [m for m in last if m.role == "user"]
        assert user_msgs, "no user message"
        return "".join(
            b.text for b in user_msgs[-1].content if isinstance(b, TextBlock)
        )


def _harness_with_multi_replies(
    replies: list[str],
) -> tuple[Harness, _MultiReplyStreamer]:
    streamer = _MultiReplyStreamer(replies)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return Harness(HarnessConfig(_engine_factory=_factory)), streamer  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Helpers for valid/invalid replies for each head type
# ---------------------------------------------------------------------------


def _valid_planner_reply(
    spec: str = "# Plan\n\nDo the thing.",
    steps: list[dict[str, object]] | None = None,
) -> str:
    if steps is None:
        steps = [
            {
                "id": "s1",
                "description": "do thing one",
                "acceptance_criteria": ["thing one is done"],
                "sprint_target": None,
                "notes": "",
            }
        ]
    return json.dumps(
        {
            "spec_markdown": spec,
            "ledger": {"steps": steps, "evaluator_enabled": True},
        }
    )


def _valid_verdict_reply(outcome: str = "pass") -> str:
    return json.dumps(
        {"outcome": outcome, "score": 0.0, "notes": "", "items": []}
    )


def _invalid_planner_reply() -> str:
    return "I forgot the envelope entirely. Here is my plan."


def _invalid_verdict_reply() -> str:
    return "Looks fine to me, no envelope."


def _contract() -> SprintContract:
    return SprintContract(
        task_id="task-001",
        sprint_number=1,
        goal="ship the widget",
        acceptance_criteria=("widget renders",),
        verification_steps=(),
        scope_includes=(),
        scope_excludes=(),
        evaluator_enabled=True,
    )


def _step() -> LedgerStep:
    return LedgerStep(id="s1", description="build widget shell")


# ===========================================================================
# GROUP 1: Core helper contract — ask_until_parsed
# ===========================================================================


class _FakeResult:
    """Minimal stand-in for RunRoleResult: just needs .final_text."""

    def __init__(self, final_text: str) -> None:
        self.final_text = final_text


class _ParseError(RuntimeError):
    """Dedicated parse-error type for helper isolation tests."""


def _good_parse(text: str) -> str:
    """Parse function that always succeeds."""
    if text.startswith("GOOD"):
        return text
    raise _ParseError(f"expected GOOD prefix, got: {text!r}")


async def test_ask_until_parsed_success_first_try_calls_ask_once() -> None:
    """On immediate parse success, ask is called exactly once."""
    from dream.runner.envelopes import ask_until_parsed

    ask = AsyncMock(return_value=_FakeResult("GOOD response"))
    result = await ask_until_parsed(
        ask, _good_parse, prompt="do it", parse_error=_ParseError
    )
    assert result == "GOOD response"
    assert ask.call_count == 1


async def test_ask_until_parsed_success_first_try_no_on_retry_called() -> None:
    """on_retry is never invoked when the first attempt parses cleanly."""
    from dream.runner.envelopes import ask_until_parsed

    on_retry = MagicMock()
    ask = AsyncMock(return_value=_FakeResult("GOOD first"))
    await ask_until_parsed(
        ask, _good_parse, prompt="p", parse_error=_ParseError, on_retry=on_retry
    )
    on_retry.assert_not_called()


async def test_ask_until_parsed_fails_once_then_succeeds() -> None:
    """First ask fails parse; second ask succeeds. Two total calls."""
    from dream.runner.envelopes import ask_until_parsed

    ask = AsyncMock(
        side_effect=[_FakeResult("BAD response"), _FakeResult("GOOD response")]
    )
    result = await ask_until_parsed(
        ask, _good_parse, prompt="do it", parse_error=_ParseError
    )
    assert result == "GOOD response"
    assert ask.call_count == 2


async def test_ask_until_parsed_second_prompt_contains_error_message() -> None:
    """The retry prompt must include the parse error message."""
    from dream.runner.envelopes import ask_until_parsed

    prompts_seen: list[str] = []

    async def _capturing_ask(prompt: str) -> _FakeResult:
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return _FakeResult("BAD response")
        return _FakeResult("GOOD response")

    await ask_until_parsed(
        _capturing_ask, _good_parse, prompt="ORIGINAL", parse_error=_ParseError
    )

    assert len(prompts_seen) == 2
    assert "ORIGINAL" in prompts_seen[1]
    # The parse error message must appear verbatim in the retry prompt.
    assert "expected GOOD prefix" in prompts_seen[1]


async def test_ask_until_parsed_second_prompt_contains_previous_reply() -> None:
    """The retry prompt must include the previous (bad) reply."""
    from dream.runner.envelopes import ask_until_parsed

    prompts_seen: list[str] = []

    async def _capturing_ask(prompt: str) -> _FakeResult:
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return _FakeResult("BAD response")
        return _FakeResult("GOOD response")

    await ask_until_parsed(
        _capturing_ask, _good_parse, prompt="ORIGINAL", parse_error=_ParseError
    )

    # The previous reply must appear in the feedback prompt.
    assert "BAD response" in prompts_seen[1]


async def test_ask_until_parsed_second_prompt_has_previous_reply_tag() -> None:
    """The retry prompt wraps the previous reply in <previous-reply> tags."""
    from dream.runner.envelopes import ask_until_parsed

    prompts_seen: list[str] = []

    async def _capturing_ask(prompt: str) -> _FakeResult:
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return _FakeResult("BAD response")
        return _FakeResult("GOOD response")

    await ask_until_parsed(
        _capturing_ask, _good_parse, prompt="ORIGINAL", parse_error=_ParseError
    )

    assert "<previous-reply>" in prompts_seen[1]
    assert "</previous-reply>" in prompts_seen[1]
    assert "JSON matching the response schema" in prompts_seen[1]
    assert "envelope" not in prompts_seen[1].lower()


async def test_ask_until_parsed_session_reuse_sends_short_correction() -> None:
    """With a shared session, retry is a short correction (no full re-paste)."""
    from dream.runner.envelopes import ask_until_parsed

    prompts_seen: list[str] = []

    async def _capturing_ask(prompt: str) -> _FakeResult:
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return _FakeResult("BAD response")
        return _FakeResult("GOOD response")

    await ask_until_parsed(
        _capturing_ask,
        _good_parse,
        prompt="ORIGINAL BEAT PACKET",
        parse_error=_ParseError,
        session_reuse=True,
    )

    assert prompts_seen[0] == "ORIGINAL BEAT PACKET"
    assert "ORIGINAL BEAT PACKET" not in prompts_seen[1]
    assert "<previous-reply>" not in prompts_seen[1]
    assert "JSON matching the response schema" in prompts_seen[1]
    assert "expected GOOD prefix" in prompts_seen[1]
    # Short correction: do not re-quote the rejected body as a previous-reply block.
    assert prompts_seen[1].count("BAD response") == 1  # only inside the error str


async def test_ask_until_parsed_on_retry_called_with_attempt_and_error() -> None:
    """on_retry receives (1, error_instance) on first retry."""
    from dream.runner.envelopes import ask_until_parsed

    on_retry_calls: list[tuple[int, _ParseError]] = []

    def _on_retry(attempt: int, err: _ParseError) -> None:
        on_retry_calls.append((attempt, err))

    ask = AsyncMock(
        side_effect=[_FakeResult("BAD response"), _FakeResult("GOOD response")]
    )
    await ask_until_parsed(
        ask,
        _good_parse,
        prompt="p",
        parse_error=_ParseError,
        on_retry=_on_retry,
    )

    assert len(on_retry_calls) == 1
    attempt, err = on_retry_calls[0]
    assert attempt == 1
    assert isinstance(err, _ParseError)


async def test_ask_until_parsed_exhausted_raises_last_error() -> None:
    """After retries=2 (3 total asks) all failing, last ParseError is re-raised."""
    from dream.runner.envelopes import ask_until_parsed

    ask = AsyncMock(
        side_effect=[
            _FakeResult("BAD 1"),
            _FakeResult("BAD 2"),
            _FakeResult("BAD 3"),
        ]
    )
    with pytest.raises(_ParseError, match="expected GOOD prefix"):
        await ask_until_parsed(
            ask, _good_parse, prompt="p", parse_error=_ParseError, retries=2
        )
    assert ask.call_count == 3


async def test_ask_until_parsed_exhausted_reraises_last_not_first() -> None:
    """The LAST ParseError is re-raised (not the first)."""
    from dream.runner.envelopes import ask_until_parsed

    errors: list[_ParseError] = []
    original_parse = _good_parse

    def _tracking_parse(text: str) -> str:
        try:
            return original_parse(text)
        except _ParseError as e:
            errors.append(e)
            raise

    ask = AsyncMock(
        side_effect=[
            _FakeResult("BAD 1"),
            _FakeResult("BAD 2"),
            _FakeResult("BAD 3"),
        ]
    )
    with pytest.raises(_ParseError) as exc_info:
        await ask_until_parsed(
            ask, _tracking_parse, prompt="p", parse_error=_ParseError, retries=2
        )

    # The re-raised error should be the last one raised by parse.
    assert exc_info.value is errors[-1]


async def test_ask_until_parsed_on_retry_called_twice_on_two_failures() -> None:
    """With retries=2, on_retry is called twice (attempt 1 and 2)."""
    from dream.runner.envelopes import ask_until_parsed

    on_retry_calls: list[int] = []

    def _on_retry(attempt: int, err: _ParseError) -> None:
        on_retry_calls.append(attempt)

    ask = AsyncMock(
        side_effect=[
            _FakeResult("BAD 1"),
            _FakeResult("BAD 2"),
            _FakeResult("GOOD 3"),
        ]
    )
    result = await ask_until_parsed(
        ask,
        _good_parse,
        prompt="p",
        parse_error=_ParseError,
        retries=2,
        on_retry=_on_retry,
    )
    assert result == "GOOD 3"
    assert on_retry_calls == [1, 2]


async def test_ask_until_parsed_non_parse_exception_propagates_immediately() -> None:
    """A non-ParseError exception from ask propagates without any retry."""
    from dream.runner.envelopes import ask_until_parsed

    on_retry = MagicMock()
    ask = AsyncMock(side_effect=ValueError("network failure"))

    with pytest.raises(ValueError, match="network failure"):
        await ask_until_parsed(
            ask,
            _good_parse,
            prompt="p",
            parse_error=_ParseError,
            on_retry=on_retry,
        )
    assert ask.call_count == 1
    on_retry.assert_not_called()


async def test_ask_until_parsed_non_parse_exception_from_ask_on_retry_propagates() -> None:
    """If ask raises a non-parse error on a retry attempt, it propagates immediately."""
    from dream.runner.envelopes import ask_until_parsed

    ask = AsyncMock(
        side_effect=[
            _FakeResult("BAD"),  # first call: bad parse
            ValueError("engine blew up"),  # second call: engine error
        ]
    )
    with pytest.raises(ValueError, match="engine blew up"):
        await ask_until_parsed(
            ask, _good_parse, prompt="p", parse_error=_ParseError
        )
    assert ask.call_count == 2


async def test_ask_until_parsed_retries_zero_raises_immediately_on_first_failure() -> None:
    """With retries=0, a single failure raises immediately (one total ask)."""
    from dream.runner.envelopes import ask_until_parsed

    ask = AsyncMock(side_effect=[_FakeResult("BAD")])
    with pytest.raises(_ParseError):
        await ask_until_parsed(
            ask, _good_parse, prompt="p", parse_error=_ParseError, retries=0
        )
    assert ask.call_count == 1


async def test_ask_until_parsed_default_retries_is_2() -> None:
    """Default retries constant is 2 (3 total asks before exhaustion)."""
    from dream.runner.envelopes import DEFAULT_RETRIES

    assert DEFAULT_RETRIES == 2


# ===========================================================================
# GROUP 2: Planner head wired through ask_until_parsed
# ===========================================================================


async def test_planner_head_succeeds_on_first_try_one_call() -> None:
    """Good reply on first ask: head parses cleanly, single run_role call."""
    harness, streamer = _harness_with_multi_replies([_valid_planner_reply()])
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert isinstance(out, PlannerOutput)
    assert streamer.call_count == 1


async def test_planner_head_recovers_from_bad_then_good_reply() -> None:
    """Bad ledger on first ask, valid reply on second ask: head returns PlannerOutput."""
    harness, streamer = _harness_with_multi_replies(
        [
            _invalid_planner_reply(),  # first: no envelope
            _valid_planner_reply(),    # second: good
        ]
    )
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert isinstance(out, PlannerOutput)
    assert streamer.call_count == 2


async def test_planner_head_retry_prompt_contains_error_and_previous_reply() -> None:
    """The retry prompt fed to run_role includes the parse error + previous reply."""
    bad_reply = _invalid_planner_reply()
    harness, streamer = _harness_with_multi_replies(
        [bad_reply, _valid_planner_reply()]
    )
    head = make_planner_head(harness)

    await head("task-001", "ship it")

    # The second call's user prompt must reference the prior bad output.
    retry_prompt = streamer.last_user_text()
    assert bad_reply in retry_prompt or "previous reply" in retry_prompt.lower()


async def test_planner_head_exhaustion_raises_planner_head_parse_error() -> None:
    """Three bad planner replies (retries=2) → PlannerHeadParseError re-raised."""
    harness, streamer = _harness_with_multi_replies(
        [
            _invalid_planner_reply(),
            _invalid_planner_reply(),
            _invalid_planner_reply(),
        ]
    )
    head = make_planner_head(harness)

    with pytest.raises(PlannerHeadParseError):
        await head("task-001", "ship it")

    assert streamer.call_count == 3


async def test_planner_head_bad_ledger_schema_also_retries() -> None:
    """A structurally valid JSON but invalid ledger schema triggers retry too."""
    bad_schema_reply = json.dumps(
        {
            "spec_markdown": "# Plan",
            "ledger": {"steps": [], "evaluator_enabled": True},
        }
    )
    harness, streamer = _harness_with_multi_replies(
        [bad_schema_reply, _valid_planner_reply()]
    )
    head = make_planner_head(harness)

    out = await head("task-001", "ship it")

    assert isinstance(out, PlannerOutput)
    assert streamer.call_count == 2


async def test_planner_head_role_session_error_propagates_without_retry() -> None:
    """Engine-level RoleSessionError must never be swallowed or retried."""
    from dream.engine._events import ErrorEvent

    class _ErrorStreamer:
        async def stream_turn(
            self, messages: Sequence[ConversationMessage]
        ) -> AsyncIterator[StreamEvent]:
            yield ErrorEvent(message="upstream blew up", recoverable=False)
            yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=_ErrorStreamer(),
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    head = make_planner_head(harness)

    with pytest.raises(RoleSessionError):
        await head("task-001", "ship it")


async def test_planner_head_emits_head_retry_observer_event() -> None:
    """A head.retry observer event is emitted when the planner retries."""
    harness, _streamer = _harness_with_multi_replies(
        [_invalid_planner_reply(), _valid_planner_reply()]
    )
    observer = CapturingObserver()
    head = make_planner_head(harness, observer=observer)

    await head("task-001", "ship it")

    retry_events = [e for e in observer.events if isinstance(e, HeadRetry)]
    assert len(retry_events) == 1
    ev = retry_events[0]
    assert ev.role == "planner"
    assert ev.attempt == 1
    assert isinstance(ev.error, str)


async def test_planner_head_no_retry_event_when_first_try_succeeds() -> None:
    """No head.retry event is emitted when the first ask succeeds."""
    harness, _streamer = _harness_with_multi_replies([_valid_planner_reply()])
    observer = CapturingObserver()
    head = make_planner_head(harness, observer=observer)

    await head("task-001", "ship it")

    retry_events = [e for e in observer.events if isinstance(e, HeadRetry)]
    assert retry_events == []


# ===========================================================================
# GROUP 3: Evaluator head wired through ask_until_parsed
# ===========================================================================


async def test_evaluator_head_recovers_from_bad_then_good_reply() -> None:
    """Bad verdict on first ask, valid on second → EvaluationRecord returned."""
    harness, streamer = _harness_with_multi_replies(
        [_invalid_verdict_reply(), _valid_verdict_reply()]
    )
    head = make_evaluator_head(harness)

    out = await head("task-001", 1, _contract(), _step())

    assert isinstance(out, EvaluationRecord)
    assert streamer.call_count == 2


async def test_evaluator_head_exhaustion_raises_evaluator_head_parse_error() -> None:
    """Three bad verdict replies → EvaluatorHeadParseError after 3 asks."""
    harness, streamer = _harness_with_multi_replies(
        [
            _invalid_verdict_reply(),
            _invalid_verdict_reply(),
            _invalid_verdict_reply(),
        ]
    )
    head = make_evaluator_head(harness)

    with pytest.raises(EvaluatorHeadParseError):
        await head("task-001", 1, _contract(), _step())

    assert streamer.call_count == 3


async def test_evaluator_head_emits_head_retry_observer_event() -> None:
    """head.retry event emitted with role=evaluator on retry."""
    harness, _streamer = _harness_with_multi_replies(
        [_invalid_verdict_reply(), _valid_verdict_reply()]
    )
    observer = CapturingObserver()
    head = make_evaluator_head(harness, observer=observer)

    await head("task-001", 1, _contract(), _step())

    retry_events = [e for e in observer.events if isinstance(e, HeadRetry)]
    assert len(retry_events) == 1
    assert retry_events[0].role == "evaluator"


async def test_evaluator_head_role_session_error_propagates_without_retry() -> None:
    """Engine-level RoleSessionError must propagate immediately from evaluator head."""
    from dream.engine._events import ErrorEvent

    class _ErrorStreamer:
        async def stream_turn(
            self, messages: Sequence[ConversationMessage]
        ) -> AsyncIterator[StreamEvent]:
            yield ErrorEvent(message="boom", recoverable=False)
            yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=_ErrorStreamer(),
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    head = make_evaluator_head(harness)

    with pytest.raises(RoleSessionError):
        await head("task-001", 1, _contract(), _step())


# ===========================================================================
# GROUP 4: Observer event structure verification
# ===========================================================================


async def test_head_retry_event_has_required_keys() -> None:
    """The head.retry event dict must have kind, role, attempt, and error keys."""
    harness, _streamer = _harness_with_multi_replies(
        [_invalid_planner_reply(), _valid_planner_reply()]
    )
    observer = CapturingObserver()
    head = make_planner_head(harness, observer=observer)

    await head("task-001", "ship it")

    ev = next(e for e in observer.events if isinstance(e, HeadRetry))
    assert ev.role == "planner"
    assert ev.attempt == 1
    assert isinstance(ev.error, str)


async def test_head_retry_event_error_is_string() -> None:
    """The 'error' field of head.retry must be a string (str(error))."""
    harness, _streamer = _harness_with_multi_replies(
        [_invalid_planner_reply(), _valid_planner_reply()]
    )
    observer = CapturingObserver()
    head = make_planner_head(harness, observer=observer)

    await head("task-001", "ship it")

    ev = next(e for e in observer.events if isinstance(e, HeadRetry))
    assert isinstance(ev.error, str)
    assert len(ev.error) > 0


async def test_no_observer_does_not_raise() -> None:
    """Head with observer=None must not raise when a retry occurs."""
    harness, _streamer = _harness_with_multi_replies(
        [_invalid_planner_reply(), _valid_planner_reply()]
    )
    # No observer passed — retry must still work silently.
    head = make_planner_head(harness, observer=None)

    out = await head("task-001", "ship it")
    assert isinstance(out, PlannerOutput)
