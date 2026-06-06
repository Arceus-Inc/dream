"""Spec 04 stage 4c — prompt-cache-friendly stable-first ordering.

The substrate's prompt cache rewards a byte-stable prefix. Assembly is
``[stable preamble][stable tool registry][stable loaded skills][volatile turn input]``,
and the stable portion is byte-identical across consecutive turns within
a session as long as no new skill loads. That property is the cache hit.
"""

from __future__ import annotations

from dream.engine._messages import ConversationMessage, TextBlock
from dream.services.prompt_assembly import (
    assemble_messages,
    assemble_stable_prefix,
)


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


# --- stable prefix is deterministic -----------------------------------------


def test_stable_prefix_is_str() -> None:
    prefix = assemble_stable_prefix(
        preamble="You are dream.",
        tool_registry="- read_file\n- bash",
        loaded_skill_bodies=[],
    )
    assert isinstance(prefix, str)


def test_stable_prefix_includes_all_three_sections_in_order() -> None:
    prefix = assemble_stable_prefix(
        preamble="PREAMBLE_TOKEN",
        tool_registry="REGISTRY_TOKEN",
        loaded_skill_bodies=[("alpha", "ALPHA_BODY_TOKEN")],
    )
    p_idx = prefix.index("PREAMBLE_TOKEN")
    r_idx = prefix.index("REGISTRY_TOKEN")
    s_idx = prefix.index("ALPHA_BODY_TOKEN")
    assert p_idx < r_idx < s_idx


# --- byte-stability across turns --------------------------------------------


def test_prompt_stable_portion_byte_stable_across_turns() -> None:
    """Two consecutive turns with no new skills loaded — bytes identical."""
    inputs = {
        "preamble": "P",
        "tool_registry": "T",
        "loaded_skill_bodies": [("alpha", "A body"), ("beta", "B body")],
    }
    p1 = assemble_stable_prefix(**inputs)
    p2 = assemble_stable_prefix(**inputs)
    assert p1.encode("utf-8") == p2.encode("utf-8")


def test_stable_prefix_is_order_independent_for_skills() -> None:
    """Loading-order must not affect bytes: registry sorts skill names."""
    p1 = assemble_stable_prefix(
        preamble="P",
        tool_registry="T",
        loaded_skill_bodies=[("alpha", "A"), ("beta", "B")],
    )
    p2 = assemble_stable_prefix(
        preamble="P",
        tool_registry="T",
        loaded_skill_bodies=[("beta", "B"), ("alpha", "A")],
    )
    assert p1 == p2


def test_loading_a_new_skill_changes_the_prefix() -> None:
    """If a new skill body joins, the prefix must change (cache reset is OK then)."""
    p1 = assemble_stable_prefix(
        preamble="P",
        tool_registry="T",
        loaded_skill_bodies=[("alpha", "A")],
    )
    p2 = assemble_stable_prefix(
        preamble="P",
        tool_registry="T",
        loaded_skill_bodies=[("alpha", "A"), ("beta", "B")],
    )
    assert p1 != p2


def test_empty_skill_list_still_produces_stable_prefix() -> None:
    p = assemble_stable_prefix(
        preamble="P",
        tool_registry="T",
        loaded_skill_bodies=[],
    )
    assert "P" in p
    assert "T" in p


# --- assemble_messages: stable prefix comes BEFORE volatile turn input ------


def test_assemble_messages_places_stable_prefix_before_turn_input() -> None:
    prefix = assemble_stable_prefix(
        preamble="P",
        tool_registry="T",
        loaded_skill_bodies=[],
    )
    messages = assemble_messages(prefix=prefix, turn_input=[_user("hello")])
    assert len(messages) >= 2
    # Concatenate the text of the first message; the prefix lives there.
    head_text = "".join(
        block.text for block in messages[0].content if isinstance(block, TextBlock)
    )
    assert "P" in head_text and "T" in head_text


def test_assemble_messages_preserves_turn_input_after_prefix() -> None:
    prefix = "STABLE"
    user_msg = _user("turn-specific question")
    messages = assemble_messages(prefix=prefix, turn_input=[user_msg])
    assert messages[-1] is user_msg


def test_assemble_messages_with_empty_turn_input_still_emits_prefix() -> None:
    messages = assemble_messages(prefix="STABLE", turn_input=[])
    assert len(messages) == 1
    assert "STABLE" in messages[0].content[0].text  # type: ignore[union-attr]
