"""LLM + deterministic compaction summariser (Spec 04 tier-2 prompt owner).

Hermes contracts lifted here (orchestration shape only — not the Hermes class tree):

- Structured rolling summary with ``_previous_summary`` carry-forward
- Head/tail stay verbatim upstream via ``split_preserving_tool_pairs``
- Cheap text extraction before any provider call
- TODO.md pending items reinjected after a lossy (full) compact boundary

The orchestrator injects the returned ``SummariserFn``; this module owns the
prompt + wire call, not the tier policy.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.prompts.cache_control import (
    OpenAIChatMessage,
    apply_cache_control,
    split_stable_system_prefix,
)
from dream.services.compact._carryover_state import CarryoverMetadata

_COMPACTION_SYSTEM = """\
You compress agent conversation history into a durable rolling summary.

Output exactly these markdown sections (omit empty sections):
## Goal
## Completed
## Key files
## Next steps
## Open questions

Preserve concrete file paths, test names, error messages, and decisions.
Be concise; do not invent facts not present in the transcript."""

_USER_PROMPT_TEMPLATE = """\
{previous_block}{workspace_block}Transcript segment to compress (older messages only; recent tail is kept verbatim elsewhere):

{transcript}
"""


@dataclass(frozen=True, slots=True)
class CompactionPromptParts:
    """Assembler slices reused by the compaction summariser call.

    ``stable_prefix`` is Dream-owned common standing orders only (no phase
    chapter). ``workspace_context`` is AGENTS/catalogues/governance for the
    user message — never mixed into the system cache prefix.
    """

    stable_prefix: str = ""
    workspace_context: str = ""
    prompt_cache: bool = False


def render_transcript_excerpt(messages: Sequence[ConversationMessage]) -> str:
    """Flatten older messages into plain text for the summariser prompt."""
    lines: list[str] = []
    for msg in messages:
        role = msg.role.upper()
        if msg.text.strip():
            lines.append(f"{role}: {msg.text.strip()}")
        for block in msg.content:
            if isinstance(block, TextBlock):
                continue  # already captured via msg.text
            name = type(block).__name__
            snippet = _block_snippet(block)
            if snippet is not None and snippet.strip():
                text = snippet.strip()
                if len(text) > 500:
                    text = text[:500] + "…"
                lines.append(f"{role} [{name}]: {text}")
    return "\n".join(lines).strip()


def _block_snippet(block: object) -> str | None:
    if isinstance(block, ToolUseBlock):
        if block.input:
            payload = json.dumps(block.input, ensure_ascii=False, default=str)
            return f"{block.name} {payload}"
        return block.name
    if isinstance(block, ToolResultBlock):
        return block.content
    return None


def build_summary_messages(summary_text: str) -> list[ConversationMessage]:
    """Wrap structured summary text as the post-boundary user message."""
    body = summary_text.strip()
    if not body:
        body = "[Compaction summary unavailable — continue from preserved attachments.]"
    preamble = (
        "[Compaction summary — reference only]\n"
        "Earlier turns were summarized. Treat attachments + this summary as continuity; "
        "do not assume verbatim tool output from before the boundary still exists.\n\n"
    )
    return [ConversationMessage(role="user", content=[TextBlock(text=preamble + body)])]


def _previous_summary_block(state: CarryoverMetadata) -> str:
    previous = (state.previous_summary or "").strip()
    if not previous:
        return ""
    return f"Previous rolling summary:\n{previous}\n\n"


def _workspace_context_block(workspace_context: str) -> str:
    text = workspace_context.strip()
    if not text:
        return ""
    return f"Workspace context (reference only):\n{text}\n\n"


def _compaction_system_message(stable_prefix: str) -> str:
    """Compose summariser system text: Dream-owned stable prefix + compact instructions."""
    prefix = stable_prefix.strip()
    if not prefix:
        return _COMPACTION_SYSTEM
    return f"{prefix}\n\n{_COMPACTION_SYSTEM}"


def make_deterministic_summariser(
    state: CarryoverMetadata,
    *,
    max_chars: int = 4_000,
) -> Callable[[list[ConversationMessage]], list[ConversationMessage]]:
    """Cheap summariser for tests and offline paths — no provider I/O."""

    def _summarise(older: list[ConversationMessage]) -> list[ConversationMessage]:
        excerpt = render_transcript_excerpt(older)
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars] + "…"
        sections = [
            "## Goal",
            "(see transcript excerpt)",
            "## Completed",
            excerpt or "(empty segment)",
        ]
        if state.previous_summary:
            sections.extend(["## Prior context", state.previous_summary[:800]])
        summary_text = "\n".join(sections)
        state.previous_summary = summary_text
        return build_summary_messages(summary_text)

    return _summarise


def make_llm_summariser(
    *,
    api_key: str,
    base_url: str,
    model: str,
    state: CarryoverMetadata,
    timeout_seconds: float = 90.0,
    prompt_parts: CompactionPromptParts | None = None,
) -> Callable[[list[ConversationMessage]], list[ConversationMessage]]:
    """Build a sync ``SummariserFn`` that calls the configured chat endpoint once.

    ``prompt_parts.stable_prefix`` is the Dream-owned common ``<stable>`` block.
    Workspace material rides ``prompt_parts.workspace_context`` in the user
    message. When ``prompt_parts.prompt_cache`` is set, Hermes cache markers
    are applied so compact can share the live-turn cache prefix.
    """

    import httpx

    from dream.api._wire import apply_token_limit

    parts = prompt_parts or CompactionPromptParts()
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    system_content = _compaction_system_message(parts.stable_prefix)

    def _summarise(older: list[ConversationMessage]) -> list[ConversationMessage]:
        excerpt = render_transcript_excerpt(older)
        if not excerpt.strip():
            return build_summary_messages("## Goal\n(continuing prior work)")

        user_content = _USER_PROMPT_TEMPLATE.format(
            previous_block=_previous_summary_block(state),
            workspace_block=_workspace_context_block(parts.workspace_context),
            transcript=excerpt,
        )
        envelopes: tuple[OpenAIChatMessage, ...] = (
            OpenAIChatMessage(role="system", content=system_content),
            OpenAIChatMessage(role="user", content=user_content),
        )
        if parts.prompt_cache:
            split = split_stable_system_prefix(system_content)
            envelopes = apply_cache_control(
                envelopes,
                static_system_prefix=split.prefix,
            )
        body = apply_token_limit(
            {
                "model": model,
                "messages": [message.to_json_object() for message in envelopes],
                "stream": False,
            },
            model,
        )

        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()

        summary_text = _summary_text_from_payload(payload)
        state.previous_summary = summary_text
        return build_summary_messages(summary_text)

    return _summarise


def _summary_text_from_payload(payload: Mapping[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("compaction summariser returned no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise RuntimeError("compaction summariser returned malformed choices")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("compaction summariser returned malformed message")
    content = message.get("content")
    summary_text = str(content or "").strip()
    if not summary_text:
        raise RuntimeError("compaction summariser returned empty content")
    return summary_text


def parse_todo_pending(
    working_dir: Path,
    *,
    todo_path: str = "TODO.md",
) -> list[str]:
    """Return unchecked TODO item texts from the workspace checklist."""
    path = working_dir / todo_path
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    pending: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            item = stripped[len("- [ ]") :].strip()
            if item:
                pending.append(item)
    return pending


def inject_todo_snapshot(
    messages: list[ConversationMessage],
    working_dir: Path | None,
    *,
    todo_path: str = "TODO.md",
) -> list[ConversationMessage]:
    """Append a TODO snapshot user message after a full-tier compact (Hermes reinject)."""
    if working_dir is None:
        return messages
    pending = parse_todo_pending(working_dir, todo_path=todo_path)
    if not pending:
        return messages
    lines = ["[TODO snapshot — pending items reinjected after compaction]"]
    lines.extend(f"- [ ] {item}" for item in pending)
    snapshot = ConversationMessage(role="user", content=[TextBlock(text="\n".join(lines))])
    return [*messages, snapshot]


__all__ = [
    "CompactionPromptParts",
    "build_summary_messages",
    "inject_todo_snapshot",
    "make_deterministic_summariser",
    "make_llm_summariser",
    "parse_todo_pending",
    "render_transcript_excerpt",
]
