"""Preserved-fields contract attachments (Spec 04 #5/#6).

The post-compact transcript is *reconstructed* from these typed attachments
rather than trusting the summariser to have preserved the contract: eight typed
factories realise the preserved-fields contract (exec plan, blocked steps,
failing tests, modified files, open hooks, orientation brief, core beliefs,
house rules), and the assembly helpers turn them into the boundary + attachment
messages the model sees.

Carryover-metadata shape read by the factories below (all keys optional;
``dict[str, Any]`` so the engine can thread arbitrary continuity state)::

    {
        "exec_plan_filename": str,
        "exec_plan_current_step": str,
        "blocked_steps": [{"step_id": str, "reason": str}, ...],
        "failing_tests": [str, ...],
        "modified_file_paths": [str, ...],
        "open_hooks": [str, ...],
        "orientation_brief": str,
        "core_beliefs_digest": str,
        "house_rules": str,
        # boundary-marker keys (create_compact_boundary_message):
        "trigger": str, "tier": str,
        "pre_compact_message_count": int, "pre_compact_token_count": int,
        "post_compact_message_count": int, "post_compact_token_count": int,
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dream.engine._messages import ConversationMessage, TextBlock
from dream.services.context_log import CompactTier, CompactTrigger


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

    kind: str
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    metadata: dict[str, Any],  # carryover metadata; see module docstring for shape
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
    """Marker message inserted at the compaction boundary.

    Reads the boundary-marker keys of the carryover metadata (``trigger``,
    ``tier``, ``pre_/post_compact_message_count``, ``pre_/post_compact_token_count``);
    see the module docstring for the full shape.
    """
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


__all__ = [
    "CompactAttachment",
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
