"""Spec 04 stage 4b — context compaction primitives (microcompact, attachments, PTL).

Two-tier compaction (Spec 04 #2): the cheap *microcompact* tier drops the
content of old, droppable ``ToolResultBlock``s and the *full* tier (deferred
to 4c — it owns the LLM prompt and the read-loop wiring) summarises the older
segment. This package is the deterministic, pure-function backbone both tiers
sit on.

The implementation is split across submodules; this ``__init__`` re-exports
every public name so importers keep using ``from dream.services.compact import …``
unchanged:

- :mod:`._microcompact` — :data:`COMPACTABLE_TOOLS`,
  :data:`TIME_BASED_MC_CLEARED_MESSAGE`, :func:`collect_compactable_tool_ids`,
  :func:`microcompact_messages` (no LLM, pure transform — *returns new
  messages, never mutates input*).
- :mod:`._boundary` — :func:`boundary_crosses_tool_pair` +
  :func:`split_preserving_tool_pairs` — the atom-safe boundary guard
  (Spec 00 #1, enforced at the most dangerous site).
- :mod:`._attachments` — :class:`CompactAttachment`, :class:`CompactionResult`,
  the eight typed factories that realise the Spec 04 #5 preserved-fields
  contract, and :func:`build_compact_attachments` /
  :func:`render_compact_attachment` / :func:`build_post_compact_messages` —
  the post-compact transcript is *reconstructed* from these (Spec 04 #6).
- :mod:`._checkpoints` — :func:`record_compact_checkpoint` (Spec 04 #8).
- :mod:`._ptl` — :func:`try_context_collapse` and
  :func:`truncate_head_for_ptl_retry` — deterministic shrink primitives the
  reactive (prompt-too-long) path uses before paying for full compaction.
"""

from __future__ import annotations

from dream.services.compact._attachments import (
    CompactAttachment,
    CompactionResult,
    build_compact_attachments,
    build_post_compact_messages,
    create_blocked_steps_attachment_if_needed,
    create_compact_boundary_message,
    create_core_beliefs_attachment_if_needed,
    create_exec_plan_attachment_if_needed,
    create_failing_tests_attachment_if_needed,
    create_house_rules_attachment_if_needed,
    create_modified_files_attachment_if_needed,
    create_open_hooks_attachment_if_needed,
    create_orientation_brief_attachment_if_needed,
    render_compact_attachment,
)
from dream.services.compact._boundary import (
    boundary_crosses_tool_pair,
    split_preserving_tool_pairs,
)
from dream.services.compact._checkpoints import record_compact_checkpoint
from dream.services.compact._microcompact import (
    COMPACTABLE_TOOLS,
    DEFAULT_KEEP_RECENT,
    TIME_BASED_MC_CLEARED_MESSAGE,
    collect_compactable_tool_ids,
    microcompact_messages,
)
from dream.services.compact._ptl import (
    CONTEXT_COLLAPSE_HEAD_CHARS,
    CONTEXT_COLLAPSE_TAIL_CHARS,
    CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT,
    PTL_RETRY_MARKER,
    truncate_head_for_ptl_retry,
    try_context_collapse,
)

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
