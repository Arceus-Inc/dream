"""Spec 04 stage 4c — stable-first prompt assembly for prompt-cache reuse.

The substrate's prompt cache rewards a byte-stable prefix. This module
assembles each turn as ``[stable preamble][stable tool registry][stable
loaded skills][volatile turn input]`` and guarantees byte-stability:

- Skill bodies are sorted by name so loading order can't shift the bytes.
- Section delimiters are fixed strings; no timestamps or per-turn ids
  leak into the stable region.
- The stable prefix is returned as one text block prepended to the turn
  input messages, so the substrate sees a single cache-prefix candidate.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.engine._messages import ConversationMessage, TextBlock

# Section markers — picked so they're impossible to confuse with skill body
# headings (``# Body``) yet still parseable by a human reading the prompt.
_PREAMBLE_HEADER = "<<< STABLE PREAMBLE >>>"
_REGISTRY_HEADER = "<<< STABLE TOOL REGISTRY >>>"
_SKILLS_HEADER = "<<< STABLE SKILL BODIES >>>"
_SKILL_DIVIDER = "---"


def assemble_stable_prefix(
    *,
    preamble: str,
    tool_registry: str,
    loaded_skill_bodies: Sequence[tuple[str, str]],
) -> str:
    """Build the byte-stable prefix string from its three sections."""
    # Sort skills by name so insertion-order changes don't shift bytes — the
    # cache hit depends on byte identity across turns.
    skill_section = ""
    if loaded_skill_bodies:
        ordered = sorted(loaded_skill_bodies, key=lambda kv: kv[0])
        rendered = [
            f"## skill: {name}\n{body.rstrip()}" for name, body in ordered
        ]
        skill_section = f"\n{_SKILL_DIVIDER}\n".join(rendered)

    parts = [
        _PREAMBLE_HEADER,
        preamble.rstrip(),
        "",
        _REGISTRY_HEADER,
        tool_registry.rstrip(),
        "",
        _SKILLS_HEADER,
        skill_section,
    ]
    return "\n".join(parts)


def assemble_messages(
    *,
    prefix: str,
    turn_input: Sequence[ConversationMessage],
) -> list[ConversationMessage]:
    """Return ``[prefix_message, *turn_input]``.

    The prefix lands in a single user message so the substrate's prompt
    cache keys on a contiguous byte range.
    """
    prefix_msg = ConversationMessage(role="user", content=[TextBlock(text=prefix)])
    return [prefix_msg, *turn_input]


__all__: list[str] = [
    "assemble_messages",
    "assemble_stable_prefix",
]
