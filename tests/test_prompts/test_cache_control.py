"""Prompt-cache breakpoints stay within the Hermes 4-marker budget."""

from __future__ import annotations

from dream.prompts.cache_control import (
    OpenAIChatMessage,
    TextContentBlock,
    apply_cache_control,
    split_stable_system_prefix,
)


def test_split_stable_prefix_from_standing_orders_block() -> None:
    prompt = "<stable>\nCOMMON\n</stable>\n\n<context>\nAGENTS\n</context>"
    split = split_stable_system_prefix(prompt)
    assert split.prefix == "<stable>\nCOMMON\n</stable>"
    assert split.prompt == prompt


def test_apply_cache_control_marks_system_and_tail() -> None:
    system_text = "<stable>\nS\n</stable>\n\n<context>\nC\n</context>"
    messages = (
        OpenAIChatMessage(role="system", content=system_text),
        OpenAIChatMessage(role="user", content="one"),
        OpenAIChatMessage(role="assistant", content="two"),
        OpenAIChatMessage(role="user", content="three"),
    )
    split = split_stable_system_prefix(system_text)
    out = apply_cache_control(messages, static_system_prefix=split.prefix)

    system = out[0].content
    assert isinstance(system, tuple)
    assert len(system) == 2
    assert isinstance(system[0], TextContentBlock)
    assert system[0].cache_control is not None
    assert system[1].cache_control is not None
    assert system[0].cache_control.to_json_object() == {"type": "ephemeral"}

    assert isinstance(out[-1].content, tuple)
    assert out[-1].content[0].cache_control is not None
    assert isinstance(out[-2].content, tuple)
    assert out[-2].content[0].cache_control is not None
    # First user turn is outside the last-N budget.
    assert out[1].content == "one"
