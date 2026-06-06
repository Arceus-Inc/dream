"""Spec 04 stage 4b — context compaction primitives (microcompact, attachments, PTL).

Two-tier compaction (Spec 04 #2): the cheap *microcompact* tier drops the
content of old, droppable ``ToolResultBlock``s and the *full* tier (deferred
to 4c — it owns the LLM prompt and the read-loop wiring) summarises the older
segment. This module is the deterministic, pure-function backbone both tiers
sit on.

What lives here (4b — borrowed/adapted from openharness ``services/compact/``):

- :data:`COMPACTABLE_TOOLS` + :data:`TIME_BASED_MC_CLEARED_MESSAGE` sentinel;
  :func:`collect_compactable_tool_ids` and :func:`microcompact_messages` (no
  LLM, pure transform — *returns new messages, never mutates input*).
- :func:`boundary_crosses_tool_pair` + :func:`split_preserving_tool_pairs` —
  the atom-safe boundary guard (Spec 00 #1, enforced at the most dangerous
  site).
- :class:`CompactAttachment`, eight typed factories that realise the Spec
  04 #5 preserved-fields contract (exec plan, blocked steps, failing tests,
  modified files, open hooks, orientation brief, core beliefs, house
  rules), and :func:`build_compact_attachments` /
  :func:`render_compact_attachment` / :func:`build_post_compact_messages` —
  the post-compact transcript is *reconstructed* from these (Spec 04 #6:
  the contract is enforced by re-emission, not by hoping the summariser
  preserved it).
- :class:`CompactionResult` — the structured handoff the orchestrator (4c)
  will hand the engine.
- :func:`record_compact_checkpoint` — append a recoverable checkpoint trail
  to carryover metadata (Spec 04 #8).
- :func:`try_context_collapse` and :func:`truncate_head_for_ptl_retry` —
  deterministic shrink primitives the reactive (prompt-too-long) path uses
  before paying for full compaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from dream.services.context_log import CompactTier, CompactTrigger
from dream.services.token_estimation import estimate_conversation_tokens, estimate_tokens
from dream.services.tool_outputs import is_microcompactable_tool_result

# --- constants ---------------------------------------------------------------

# Canonical local tools whose old results are always safe to drop. MCP results
# and large non-MCP results also become eligible via the
# ``is_microcompactable_tool_result`` predicate.
COMPACTABLE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "bash",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
        "edit_file",
        "write_file",
    }
)

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"
DEFAULT_KEEP_RECENT = 5

# Char limits for try_context_collapse. Picked to match openharness so the
# behaviour is identical when both harnesses see the same transcript.
CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT = 2_400
CONTEXT_COLLAPSE_HEAD_CHARS = 900
CONTEXT_COLLAPSE_TAIL_CHARS = 500

PTL_RETRY_MARKER = "[earlier conversation truncated for compaction retry]"


# --- shapes ------------------------------------------------------------------


@dataclass(frozen=True)
class CompactAttachment:
    """A typed asset that survives a compaction boundary.

    Realises Spec 04 #6: the contract is enforced by reconstruction.
    """

    kind: str
    title: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompactionResult:
    """Structured handoff between the compactor and the engine."""

    trigger: CompactTrigger
    tier: CompactTier
    boundary_marker: ConversationMessage
    summary_messages: list[ConversationMessage]
    messages_to_keep: list[ConversationMessage]
    attachments: list[CompactAttachment]
    metadata: dict[str, Any] = field(default_factory=dict)


# --- microcompact ------------------------------------------------------------


def collect_compactable_tool_ids(messages: list[ConversationMessage]) -> list[str]:
    """Walk messages and collect tool_use IDs whose results are compactable.

    Order is chronological (oldest first) so callers can keep the most recent
    ``keep_recent`` and clear the rest.
    """
    ordered_ids: list[str] = []
    tool_names: dict[str, str] = {}
    result_content: dict[str, str] = {}
    for msg in messages:
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                ordered_ids.append(block.id)
                tool_names[block.id] = block.name
            elif isinstance(block, ToolResultBlock):
                result_content[block.tool_use_id] = block.content
    return [
        tool_id
        for tool_id in ordered_ids
        if tool_names.get(tool_id, "") in COMPACTABLE_TOOLS
        or is_microcompactable_tool_result(
            tool_names.get(tool_id, ""),
            result_content.get(tool_id, ""),
        )
    ]


def microcompact_messages(
    messages: list[ConversationMessage],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[ConversationMessage], int]:
    """Replace old compactable tool-result content with a sentinel.

    Returns a new list (input is not mutated) plus the estimated tokens
    reclaimed. ``keep_recent`` is clamped to ``max(1, keep_recent)`` so we
    never erase every tool result — at least one anchor stays.
    """
    keep_recent = max(1, keep_recent)
    all_ids = collect_compactable_tool_ids(messages)

    if len(all_ids) <= keep_recent:
        return list(messages), 0

    keep_set = set(all_ids[-keep_recent:])
    clear_set = set(all_ids) - keep_set

    tokens_saved = 0
    out: list[ConversationMessage] = []
    for msg in messages:
        if msg.role != "user":
            out.append(msg)
            continue
        new_content: list[ContentBlock] = []
        changed = False
        for block in msg.content:
            if (
                isinstance(block, ToolResultBlock)
                and block.tool_use_id in clear_set
                and block.content != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                tokens_saved += estimate_tokens(block.content)
                new_content.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=TIME_BASED_MC_CLEARED_MESSAGE,
                        is_error=block.is_error,
                    )
                )
                changed = True
            else:
                new_content.append(block)
        out.append(
            ConversationMessage(role=msg.role, content=new_content) if changed else msg
        )

    return out, tokens_saved


# --- atom-safe boundary ------------------------------------------------------


def boundary_crosses_tool_pair(
    previous: ConversationMessage, current: ConversationMessage
) -> bool:
    """True when a preserve boundary would split a tool_use/result pair."""
    if previous.role != "assistant" or current.role != "user":
        return False
    pending_tool_ids = {
        block.id for block in previous.content if isinstance(block, ToolUseBlock)
    }
    if not pending_tool_ids:
        return False
    result_ids = {
        block.tool_use_id
        for block in current.content
        if isinstance(block, ToolResultBlock)
    }
    return bool(pending_tool_ids & result_ids)


def split_preserving_tool_pairs(
    messages: list[ConversationMessage], *, preserve_recent: int
) -> tuple[list[ConversationMessage], list[ConversationMessage]]:
    """Split older/newer without cutting through a tool_use/result pair.

    The newer segment is also sanitised so a trailing orphan ``ToolUseBlock``
    never survives the boundary.
    """
    if len(messages) <= preserve_recent:
        return [], sanitize_conversation_messages(list(messages))

    split_index = max(0, len(messages) - preserve_recent)
    while split_index > 0 and boundary_crosses_tool_pair(
        messages[split_index - 1], messages[split_index]
    ):
        split_index -= 1

    older = list(messages[:split_index])
    newer = sanitize_conversation_messages(list(messages[split_index:]))
    return older, newer


# --- attachment factories (contract per Spec 04 #5) -------------------------


def _attachment(
    kind: str,
    title: str,
    lines: list[str],
    *,
    metadata: dict[str, Any] | None = None,
) -> CompactAttachment | None:
    filtered = [line.rstrip() for line in lines if line and line.strip()]
    if not filtered:
        return None
    return CompactAttachment(
        kind=kind,
        title=title,
        body="\n".join(filtered),
        metadata=dict(metadata or {}),
    )


def create_exec_plan_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: exec-plan filename + current step survive verbatim."""
    filename = str(metadata.get("exec_plan_filename") or "").strip()
    current_step = str(metadata.get("exec_plan_current_step") or "").strip()
    if not filename and not current_step:
        return None
    lines = ["Current execution plan in flight:"]
    if filename:
        lines.append(f"- Plan file: {filename}")
    if current_step:
        lines.append(f"- Current step: {current_step}")
    return _attachment(
        "exec_plan",
        "Current execution plan",
        lines,
        metadata={"filename": filename, "current_step": current_step},
    )


def create_blocked_steps_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: every blocked step + its blocked_reason survives."""
    blocked = metadata.get("blocked_steps") or []
    if not isinstance(blocked, list) or not blocked:
        return None
    entries: list[dict[str, Any]] = []
    lines = ["Blocked steps requiring attention before progress can resume:"]
    for entry in blocked:
        if not isinstance(entry, dict):
            continue
        step_id = str(entry.get("step_id") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        if not step_id and not reason:
            continue
        lines.append(f"- {step_id}: {reason}")
        entries.append({"step_id": step_id, "reason": reason})
    if not entries:
        return None
    return _attachment(
        "blocked_steps",
        "Blocked steps",
        lines,
        metadata={"entries": entries},
    )


def create_failing_tests_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: every currently-failing test name survives verbatim."""
    failing = metadata.get("failing_tests") or []
    if not isinstance(failing, list) or not failing:
        return None
    names = [str(name).strip() for name in failing if str(name).strip()]
    if not names:
        return None
    return _attachment(
        "failing_tests",
        "Currently failing tests",
        ["These tests were failing at compaction time and still need to be green:"]
        + [f"- {name}" for name in names],
        metadata={"names": names},
    )


def create_modified_files_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: file paths modified this task survive."""
    paths = metadata.get("modified_file_paths") or []
    if not isinstance(paths, list) or not paths:
        return None
    normalized = [str(p).strip() for p in paths if str(p).strip()]
    if not normalized:
        return None
    return _attachment(
        "modified_files",
        "Files modified this task",
        ["These files were edited during this task:"] + [f"- {p}" for p in normalized],
        metadata={"paths": normalized},
    )


def create_open_hooks_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: open plugin hooks/handlers survive."""
    hooks = metadata.get("open_hooks") or []
    if not isinstance(hooks, list) or not hooks:
        return None
    names = [str(h).strip() for h in hooks if str(h).strip()]
    if not names:
        return None
    return _attachment(
        "open_hooks",
        "Open plugin hooks",
        ["Hooks/handlers currently registered:"] + [f"- {n}" for n in names],
        metadata={"names": names},
    )


def create_orientation_brief_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: the orientation brief survives *in full*."""
    brief = str(metadata.get("orientation_brief") or "").strip()
    if not brief:
        return None
    return _attachment(
        "orientation_brief",
        "Orientation brief",
        ["Orientation brief (preserved verbatim):", brief],
        metadata={"brief": brief},
    )


def create_core_beliefs_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: the core-beliefs digest survives."""
    digest = str(metadata.get("core_beliefs_digest") or "").strip()
    if not digest:
        return None
    return _attachment(
        "core_beliefs_digest",
        "Core beliefs digest",
        ["Core beliefs digest:", digest],
        metadata={"digest": digest},
    )


def create_house_rules_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    """Spec 04 contract: house rules survive."""
    rules = str(metadata.get("house_rules") or "").strip()
    if not rules:
        return None
    return _attachment(
        "house_rules",
        "House rules",
        ["House rules in effect:", rules],
        metadata={"rules": rules},
    )


def build_compact_attachments(metadata: dict[str, Any]) -> list[CompactAttachment]:
    """Assemble the contract attachments in Spec 04 #5 order."""
    builders = (
        create_exec_plan_attachment_if_needed,
        create_blocked_steps_attachment_if_needed,
        create_failing_tests_attachment_if_needed,
        create_modified_files_attachment_if_needed,
        create_open_hooks_attachment_if_needed,
        create_orientation_brief_attachment_if_needed,
        create_core_beliefs_attachment_if_needed,
        create_house_rules_attachment_if_needed,
    )
    return [att for builder in builders if (att := builder(metadata)) is not None]


def render_compact_attachment(attachment: CompactAttachment) -> ConversationMessage:
    """Serialize a structured attachment into a user message the model sees."""
    header = f"[Compact attachment: {attachment.kind}] {attachment.title}".strip()
    text = f"{header}\n{attachment.body}".strip()
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def create_compact_boundary_message(metadata: dict[str, Any]) -> ConversationMessage:
    """Marker message inserted at the compaction boundary."""
    lines = [
        "[Compact boundary marker]",
        "Earlier conversation was compacted. Use the summary and preserved assets below as the continuity boundary.",
    ]
    trigger = str(metadata.get("trigger") or "").strip()
    tier = str(metadata.get("tier") or "").strip()
    if trigger:
        lines.append(f"Trigger: {trigger}")
    if tier:
        lines.append(f"Compaction tier: {tier}")
    pre_messages = metadata.get("pre_compact_message_count")
    pre_tokens = metadata.get("pre_compact_token_count")
    post_messages = metadata.get("post_compact_message_count")
    post_tokens = metadata.get("post_compact_token_count")
    if pre_messages is not None or pre_tokens is not None:
        lines.append(
            "Pre-compact footprint: "
            f"messages={pre_messages if pre_messages is not None else 'unknown'}, "
            f"tokens={pre_tokens if pre_tokens is not None else 'unknown'}"
        )
    if post_messages is not None or post_tokens is not None:
        lines.append(
            "Post-compact footprint: "
            f"messages={post_messages if post_messages is not None else 'unknown'}, "
            f"tokens={post_tokens if post_tokens is not None else 'unknown'}"
        )
    return ConversationMessage(role="user", content=[TextBlock(text="\n".join(lines))])


def build_post_compact_messages(result: CompactionResult) -> list[ConversationMessage]:
    """Rebuild the post-compact transcript: boundary, summary, keep, attachments."""
    attachment_messages = [
        render_compact_attachment(attachment) for attachment in result.attachments
    ]
    return [
        result.boundary_marker,
        *result.summary_messages,
        *result.messages_to_keep,
        *attachment_messages,
    ]


# --- checkpoints -------------------------------------------------------------


def record_compact_checkpoint(
    carryover_metadata: dict[str, Any] | None,
    *,
    checkpoint: str,
    trigger: CompactTrigger,
    message_count: int,
    token_count: int,
    attempt: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured checkpoint payload and stamp it as ``compact_last``."""
    payload: dict[str, Any] = {
        "checkpoint": checkpoint,
        "trigger": trigger,
        "message_count": message_count,
        "token_count": token_count,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    if details:
        payload.update(details)
    if carryover_metadata is not None:
        checkpoints = carryover_metadata.setdefault("compact_checkpoints", [])
        if isinstance(checkpoints, list):
            checkpoints.append(payload)
        carryover_metadata["compact_last"] = payload
    return payload


# --- reactive (PTL) shrink primitives ---------------------------------------


def _collapse_text(text: str) -> str:
    if len(text) <= CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT:
        return text
    omitted = len(text) - CONTEXT_COLLAPSE_HEAD_CHARS - CONTEXT_COLLAPSE_TAIL_CHARS
    head = text[:CONTEXT_COLLAPSE_HEAD_CHARS].rstrip()
    tail = text[-CONTEXT_COLLAPSE_TAIL_CHARS:].lstrip()
    return f"{head}\n...[collapsed {omitted} chars]...\n{tail}"


def try_context_collapse(
    messages: list[ConversationMessage], *, preserve_recent: int
) -> list[ConversationMessage] | None:
    """Deterministically shrink oversized text blocks before paying for full compact.

    Returns ``None`` when there is nothing useful to collapse (so the caller
    can fall through to full compaction); otherwise returns a new message
    list whose token estimate is strictly lower.
    """
    if len(messages) <= preserve_recent + 2:
        return None

    older, newer = split_preserving_tool_pairs(messages, preserve_recent=preserve_recent)
    changed = False
    collapsed_older: list[ConversationMessage] = []
    for message in older:
        new_blocks: list[ContentBlock] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                collapsed = _collapse_text(block.text)
                if collapsed != block.text:
                    changed = True
                new_blocks.append(TextBlock(text=collapsed))
            elif isinstance(block, ToolResultBlock):
                collapsed = _collapse_text(block.content)
                if collapsed != block.content:
                    changed = True
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=collapsed,
                        is_error=block.is_error,
                    )
                )
            else:
                new_blocks.append(block)
        collapsed_older.append(ConversationMessage(role=message.role, content=new_blocks))

    if not changed:
        return None

    result = [*collapsed_older, *newer]
    if estimate_conversation_tokens(result) >= estimate_conversation_tokens(messages):
        return None
    return result


def _group_messages_by_prompt_round(
    messages: list[ConversationMessage],
) -> list[list[ConversationMessage]]:
    groups: list[list[ConversationMessage]] = []
    current: list[ConversationMessage] = []
    for message in messages:
        starts_new_round = (
            message.role == "user"
            and not any(
                isinstance(block, ToolResultBlock) for block in message.content
            )
            and bool(message.text.strip())
        )
        if starts_new_round and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def truncate_head_for_ptl_retry(
    messages: list[ConversationMessage],
) -> list[ConversationMessage] | None:
    """Drop the oldest prompt rounds when reactive compaction needs aggressive room.

    Returns ``None`` when there is only a single round (nothing safe to drop).
    Otherwise drops ``max(1, n_rounds // 5)`` of the oldest rounds. If the
    surviving head starts with an assistant message (orphaned), prepend a
    user marker so the transcript remains provider-valid.
    """
    groups = _group_messages_by_prompt_round(messages)
    if len(groups) < 2:
        return None

    drop_count = max(1, len(groups) // 5)
    drop_count = min(drop_count, len(groups) - 1)
    retained = [message for group in groups[drop_count:] for message in group]
    if not retained:
        return None
    if retained[0].role == "assistant":
        marker = ConversationMessage(
            role="user", content=[TextBlock(text=PTL_RETRY_MARKER)]
        )
        return [marker, *retained]
    return retained


__all__ = [
    "COMPACTABLE_TOOLS",
    "CONTEXT_COLLAPSE_HEAD_CHARS",
    "CONTEXT_COLLAPSE_TAIL_CHARS",
    "CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT",
    "DEFAULT_KEEP_RECENT",
    "PTL_RETRY_MARKER",
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "CompactAttachment",
    "CompactionResult",
    "boundary_crosses_tool_pair",
    "build_compact_attachments",
    "build_post_compact_messages",
    "collect_compactable_tool_ids",
    "create_blocked_steps_attachment_if_needed",
    "create_compact_boundary_message",
    "create_core_beliefs_attachment_if_needed",
    "create_exec_plan_attachment_if_needed",
    "create_failing_tests_attachment_if_needed",
    "create_house_rules_attachment_if_needed",
    "create_modified_files_attachment_if_needed",
    "create_open_hooks_attachment_if_needed",
    "create_orientation_brief_attachment_if_needed",
    "microcompact_messages",
    "record_compact_checkpoint",
    "render_compact_attachment",
    "split_preserving_tool_pairs",
    "truncate_head_for_ptl_retry",
    "try_context_collapse",
]
