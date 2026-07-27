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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from dream.engine._messages import ConversationMessage, TextBlock, ToolUseBlock
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
{previous_block}Transcript segment to compress (older messages only; recent tail is kept verbatim elsewhere):

{transcript}
"""


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
            if isinstance(block, ToolUseBlock):
                snippet = (
                    json.dumps(block.input, ensure_ascii=False)
                    if block.input
                    else block.name
                )
            else:
                snippet = getattr(block, "content", None) or getattr(block, "name", "")
            if isinstance(snippet, str) and snippet.strip():
                text = snippet.strip()
                if len(text) > 500:
                    text = text[:500] + "…"
                lines.append(f"{role} [{name}]: {text}")
    return "\n".join(lines).strip()


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
) -> Callable[[list[ConversationMessage]], list[ConversationMessage]]:
    """Build a sync ``SummariserFn`` that calls the configured chat endpoint once."""

    import httpx

    from dream.api._wire import apply_token_limit

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def _summarise(older: list[ConversationMessage]) -> list[ConversationMessage]:
        excerpt = render_transcript_excerpt(older)
        if not excerpt.strip():
            return build_summary_messages("## Goal\n(continuing prior work)")

        user_content = _USER_PROMPT_TEMPLATE.format(
            previous_block=_previous_summary_block(state),
            transcript=excerpt,
        )
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _COMPACTION_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        }
        body = apply_token_limit(body, model)

        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("compaction summariser returned no choices")
        message = choices[0].get("message") or {}
        summary_text = str(message.get("content") or "").strip()
        if not summary_text:
            raise RuntimeError("compaction summariser returned empty content")

        state.previous_summary = summary_text
        return build_summary_messages(summary_text)

    return _summarise


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
    "build_summary_messages",
    "inject_todo_snapshot",
    "make_deterministic_summariser",
    "make_llm_summariser",
    "parse_todo_pending",
    "render_transcript_excerpt",
]
