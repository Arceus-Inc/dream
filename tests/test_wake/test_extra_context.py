"""``extra_context`` rides into the wake stimulus (spec 15 hardening 2).

The wake scheduler appends queued cron notes to the heartbeat turn —
the runner must surface them in the single user message, after the
prompt body, before the decision instruction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._events import AssistantTurnComplete, StreamEvent
from dream.engine._messages import ConversationMessage, TextBlock
from dream.wake import ManualWake
from dream.wake._runner import run_background_turn


class _SilentStreamer:
    """Yields one empty assistant turn; records the stimulus it saw."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        text = "".join(
            block.text
            for message in messages
            for block in message.content
            if isinstance(block, TextBlock)
        )
        self.seen.append(text)
        yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())


@pytest.mark.asyncio
async def test_extra_context_lands_in_stimulus() -> None:
    streamer = _SilentStreamer()
    await run_background_turn(
        streamer,
        wake_source=ManualWake(),
        system_prompt="You are the heartbeat.",
        extra_context="PENDING NOTES:\n- the digest is due",
    )
    stimulus = streamer.seen[0]
    assert "the digest is due" in stimulus
    # Notes come after the prompt body and before the decision instruction.
    assert stimulus.index("You are the heartbeat.") < stimulus.index("digest is due")
    assert stimulus.index("digest is due") < stimulus.index("Decide now")


@pytest.mark.asyncio
async def test_no_extra_context_leaves_stimulus_unchanged() -> None:
    streamer = _SilentStreamer()
    await run_background_turn(
        streamer, wake_source=ManualWake(), system_prompt="You are the heartbeat."
    )
    assert "PENDING" not in streamer.seen[0]
