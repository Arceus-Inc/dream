"""Preserved-fields contract attachments (Spec 04 #5/#6).

The post-compact transcript is *reconstructed* from these typed attachments
rather than trusting the summariser to have preserved the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from dream.engine._messages import ConversationMessage, TextBlock
from dream.services.compact._carryover_state import CarryoverMetadata
from dream.services.context_log import CompactTier, CompactTrigger


class CompactAttachmentKind(StrEnum):
    EXEC_PLAN = "exec_plan"
    BLOCKED_STEPS = "blocked_steps"
    FAILING_TESTS = "failing_tests"
    MODIFIED_FILES = "modified_files"
    OPEN_HOOKS = "open_hooks"
    ORIENTATION_BRIEF = "orientation_brief"
    CORE_BELIEFS_DIGEST = "core_beliefs_digest"
    HOUSE_RULES = "house_rules"


@dataclass(frozen=True)
class CompactionBoundaryInfo:
    trigger: CompactTrigger
    tier: CompactTier
    pre_compact_message_count: int | None = None
    pre_compact_token_count: int | None = None
    post_compact_message_count: int | None = None
    post_compact_token_count: int | None = None


@dataclass(frozen=True)
class CompactAttachment:
    """A typed asset that survives a compaction boundary.

    Realises Spec 04 #6: the contract is enforced by reconstruction.

    ``metadata`` carries the structured form of ``body`` for downstream
    consumers; the recognized keys depend on ``kind`` and mirror the factory
    that built it, e.g. ``exec_plan`` -> ``{"filename": str, "current_step": str}``,
    ``blocked_steps`` -> ``{"entries": [{"step_id": str, "reason": str}, ...]}``,
    ``failing_tests``/``modified_files``/``open_hooks`` -> ``{"names"|"paths": [str]}``,
    ``orientation_brief``/``core_beliefs_digest``/``house_rules`` -> ``{"brief"|"digest"|"rules": str}``.
    """

    kind: CompactAttachmentKind
    title: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompactionResult:
    """Structured handoff between the compactor and the engine.

    ``metadata`` is an open ``dict[str, Any]`` the orchestrator stamps with at
    least ``{"tier": "microcompact"|"full"}``; consumers should treat unknown
    keys as forward-compatible extras.
    """

    trigger: CompactTrigger
    tier: CompactTier
    boundary_marker: ConversationMessage
    summary_messages: list[ConversationMessage]
    messages_to_keep: list[ConversationMessage]
    attachments: list[CompactAttachment]
    metadata: dict[str, Any] = field(default_factory=dict)


# --- attachment factories (contract per Spec 04 #5) -------------------------


def _attachment(
    kind: CompactAttachmentKind,
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
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: exec-plan filename + current step survive verbatim."""
    filename = (metadata.exec_plan_filename or "").strip()
    current_step = (metadata.exec_plan_current_step or "").strip()
    if not filename and not current_step:
        return None
    lines = ["Current execution plan in flight:"]
    if filename:
        lines.append(f"- Plan file: {filename}")
    if current_step:
        lines.append(f"- Current step: {current_step}")
    return _attachment(
        CompactAttachmentKind.EXEC_PLAN,
        "Current execution plan",
        lines,
        metadata={"filename": filename, "current_step": current_step},
    )


def create_blocked_steps_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: every blocked step + its blocked_reason survives."""
    if not metadata.blocked_steps:
        return None
    entries: list[dict[str, str]] = []
    lines = ["Blocked steps requiring attention before progress can resume:"]
    for entry in metadata.blocked_steps:
        step_id = entry.step_id.strip()
        reason = entry.reason.strip()
        if not step_id and not reason:
            continue
        lines.append(f"- {step_id}: {reason}")
        entries.append({"step_id": step_id, "reason": reason})
    if not entries:
        return None
    return _attachment(
        CompactAttachmentKind.BLOCKED_STEPS,
        "Blocked steps",
        lines,
        metadata={"entries": entries},
    )


def create_failing_tests_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: every currently-failing test name survives verbatim."""
    if not metadata.failing_tests:
        return None
    names = [name.strip() for name in metadata.failing_tests if name.strip()]
    if not names:
        return None
    return _attachment(
        CompactAttachmentKind.FAILING_TESTS,
        "Currently failing tests",
        ["These tests were failing at compaction time and still need to be green:"]
        + [f"- {name}" for name in names],
        metadata={"names": names},
    )


def create_modified_files_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: file paths modified this task survive."""
    if not metadata.modified_file_paths:
        return None
    normalized = [path.strip() for path in metadata.modified_file_paths if path.strip()]
    if not normalized:
        return None
    return _attachment(
        CompactAttachmentKind.MODIFIED_FILES,
        "Files modified this task",
        ["These files were edited during this task:"] + [f"- {p}" for p in normalized],
        metadata={"paths": normalized},
    )


def create_open_hooks_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: open plugin hooks/handlers survive."""
    if not metadata.open_hooks:
        return None
    names = [hook.strip() for hook in metadata.open_hooks if hook.strip()]
    if not names:
        return None
    return _attachment(
        CompactAttachmentKind.OPEN_HOOKS,
        "Open plugin hooks",
        ["Hooks/handlers currently registered:"] + [f"- {n}" for n in names],
        metadata={"names": names},
    )


def create_orientation_brief_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: the orientation brief survives *in full*."""
    brief = (metadata.orientation_brief or "").strip()
    if not brief:
        return None
    return _attachment(
        CompactAttachmentKind.ORIENTATION_BRIEF,
        "Orientation brief",
        ["Orientation brief (preserved verbatim):", brief],
        metadata={"brief": brief},
    )


def create_core_beliefs_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: the core-beliefs digest survives."""
    digest = (metadata.core_beliefs_digest or "").strip()
    if not digest:
        return None
    return _attachment(
        CompactAttachmentKind.CORE_BELIEFS_DIGEST,
        "Core beliefs digest",
        ["Core beliefs digest:", digest],
        metadata={"digest": digest},
    )


def create_house_rules_attachment_if_needed(
    metadata: CarryoverMetadata,
) -> CompactAttachment | None:
    """Spec 04 contract: house rules survive."""
    rules = (metadata.house_rules or "").strip()
    if not rules:
        return None
    return _attachment(
        CompactAttachmentKind.HOUSE_RULES,
        "House rules",
        ["House rules in effect:", rules],
        metadata={"rules": rules},
    )


def build_compact_attachments(metadata: CarryoverMetadata) -> list[CompactAttachment]:
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


def create_compact_boundary_message(info: CompactionBoundaryInfo) -> ConversationMessage:
    """Marker message inserted at the compaction boundary."""
    lines = [
        "[Compact boundary marker]",
        "Earlier conversation was compacted. Use the summary and preserved assets below as the continuity boundary.",
        f"Trigger: {info.trigger}",
        f"Compaction tier: {info.tier}",
    ]
    if info.pre_compact_message_count is not None or info.pre_compact_token_count is not None:
        lines.append(
            "Pre-compact footprint: "
            f"messages={info.pre_compact_message_count if info.pre_compact_message_count is not None else 'unknown'}, "
            f"tokens={info.pre_compact_token_count if info.pre_compact_token_count is not None else 'unknown'}"
        )
    if info.post_compact_message_count is not None or info.post_compact_token_count is not None:
        lines.append(
            "Post-compact footprint: "
            f"messages={info.post_compact_message_count if info.post_compact_message_count is not None else 'unknown'}, "
            f"tokens={info.post_compact_token_count if info.post_compact_token_count is not None else 'unknown'}"
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


__all__ = [
    "CompactAttachment",
    "CompactAttachmentKind",
    "CompactionBoundaryInfo",
    "CompactionResult",
    "build_compact_attachments",
    "build_post_compact_messages",
    "create_blocked_steps_attachment_if_needed",
    "create_compact_boundary_message",
    "create_core_beliefs_attachment_if_needed",
    "create_exec_plan_attachment_if_needed",
    "create_failing_tests_attachment_if_needed",
    "create_house_rules_attachment_if_needed",
    "create_modified_files_attachment_if_needed",
    "create_open_hooks_attachment_if_needed",
    "create_orientation_brief_attachment_if_needed",
    "render_compact_attachment",
]
