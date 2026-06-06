"""Spec 04 stage 4b — typed compact attachments + post-compact rebuild.

The preserved-fields contract (Spec 04 #5/#6) is enforced by *reconstruction*:
after compaction the post-compact message list is rebuilt from typed
``CompactAttachment``s, so contract fields survive because they are
re-emitted — not because they happened to escape the summariser.

Per Spec 04 #5 the contract fields MUST survive verbatim:
    - current exec-plan filename + current step
    - all blocked steps + their blocked_reason
    - currently-failing test names
    - file paths modified this task
    - open plugin hooks/handlers
    - the orientation brief in full
    - the core-beliefs digest
    - house rules
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dream.engine._messages import ConversationMessage, TextBlock
from dream.services.compact import (
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

# --- CompactAttachment shape -------------------------------------------------


def test_compact_attachment_is_frozen() -> None:
    att = CompactAttachment(kind="exec_plan", title="t", body="b")
    with pytest.raises(FrozenInstanceError):
        att.kind = "other"  # type: ignore[misc]


def test_compact_attachment_metadata_defaults_to_empty_dict() -> None:
    att = CompactAttachment(kind="exec_plan", title="t", body="b")
    assert att.metadata == {}


# --- Contract attachment factories ------------------------------------------


def test_exec_plan_factory_returns_none_when_metadata_missing() -> None:
    assert create_exec_plan_attachment_if_needed({}) is None


def test_exec_plan_factory_preserves_filename_and_current_step() -> None:
    att = create_exec_plan_attachment_if_needed(
        {"exec_plan_filename": "plans/p-001.md", "exec_plan_current_step": "step-3"}
    )
    assert att is not None
    assert att.kind == "exec_plan"
    assert "plans/p-001.md" in att.body
    assert "step-3" in att.body
    assert att.metadata["filename"] == "plans/p-001.md"


def test_blocked_steps_factory_returns_none_when_empty() -> None:
    assert create_blocked_steps_attachment_if_needed({"blocked_steps": []}) is None
    assert create_blocked_steps_attachment_if_needed({}) is None


def test_blocked_steps_factory_preserves_each_step_and_reason() -> None:
    blocked = [
        {"step_id": "s2", "reason": "waiting on review"},
        {"step_id": "s5", "reason": "test fixture not ready"},
    ]
    att = create_blocked_steps_attachment_if_needed({"blocked_steps": blocked})
    assert att is not None
    assert att.kind == "blocked_steps"
    assert "s2" in att.body and "waiting on review" in att.body
    assert "s5" in att.body and "test fixture not ready" in att.body
    assert att.metadata["entries"] == blocked


def test_failing_tests_factory_returns_none_when_empty() -> None:
    assert create_failing_tests_attachment_if_needed({"failing_tests": []}) is None


def test_failing_tests_factory_preserves_every_name() -> None:
    failing = ["tests/a.py::test_x", "tests/b.py::test_y", "tests/c.py::test_z"]
    att = create_failing_tests_attachment_if_needed({"failing_tests": failing})
    assert att is not None
    assert att.kind == "failing_tests"
    for name in failing:
        assert name in att.body


def test_modified_files_factory_preserves_paths() -> None:
    paths = ["src/foo.py", "tests/test_foo.py"]
    att = create_modified_files_attachment_if_needed({"modified_file_paths": paths})
    assert att is not None
    assert att.kind == "modified_files"
    assert all(p in att.body for p in paths)


def test_open_hooks_factory_preserves_names() -> None:
    hooks = ["pre_commit", "post_turn"]
    att = create_open_hooks_attachment_if_needed({"open_hooks": hooks})
    assert att is not None
    assert att.kind == "open_hooks"
    assert all(h in att.body for h in hooks)


def test_orientation_brief_factory_preserves_text_verbatim() -> None:
    """Spec contract: the orientation brief survives 'in full'."""
    brief = "Mission: Y. Repo: Z.\nHouse rule #1: ...\nHouse rule #2: ..."
    att = create_orientation_brief_attachment_if_needed({"orientation_brief": brief})
    assert att is not None
    assert att.kind == "orientation_brief"
    assert brief in att.body


def test_orientation_brief_factory_returns_none_for_blank() -> None:
    assert create_orientation_brief_attachment_if_needed({"orientation_brief": "   "}) is None
    assert create_orientation_brief_attachment_if_needed({}) is None


def test_core_beliefs_factory_preserves_digest() -> None:
    digest = "Believe: small, atomic, reversible."
    att = create_core_beliefs_attachment_if_needed({"core_beliefs_digest": digest})
    assert att is not None
    assert att.kind == "core_beliefs_digest"
    assert digest in att.body


def test_house_rules_factory_preserves_rules() -> None:
    rules = "1. Tests first.\n2. No prints.\n3. Atomic writes only."
    att = create_house_rules_attachment_if_needed({"house_rules": rules})
    assert att is not None
    assert att.kind == "house_rules"
    assert rules in att.body


# --- build_compact_attachments ----------------------------------------------


def test_build_compact_attachments_returns_only_present_fields() -> None:
    metadata = {
        "exec_plan_filename": "p.md",
        "exec_plan_current_step": "s1",
        # blocked_steps deliberately missing
        "failing_tests": ["t::a"],
    }
    atts = build_compact_attachments(metadata)
    kinds = [a.kind for a in atts]
    assert "exec_plan" in kinds
    assert "failing_tests" in kinds
    assert "blocked_steps" not in kinds


def test_build_compact_attachments_orders_contract_fields_first() -> None:
    """Acceptance: contract fields drive context; orientation/beliefs/rules anchor it."""
    metadata = {
        "exec_plan_filename": "p.md",
        "exec_plan_current_step": "s1",
        "blocked_steps": [{"step_id": "s2", "reason": "waiting"}],
        "failing_tests": ["t::a"],
        "modified_file_paths": ["src/a.py"],
        "open_hooks": ["pre_commit"],
        "orientation_brief": "mission",
        "core_beliefs_digest": "atomic",
        "house_rules": "tests first",
    }
    kinds = [a.kind for a in build_compact_attachments(metadata)]
    assert kinds == [
        "exec_plan",
        "blocked_steps",
        "failing_tests",
        "modified_files",
        "open_hooks",
        "orientation_brief",
        "core_beliefs_digest",
        "house_rules",
    ]


def test_build_compact_attachments_returns_empty_on_empty_metadata() -> None:
    assert build_compact_attachments({}) == []


# --- render + boundary marker + post-compact rebuild ------------------------


def test_render_compact_attachment_returns_user_message() -> None:
    msg = render_compact_attachment(
        CompactAttachment(kind="exec_plan", title="Plan", body="step body")
    )
    assert msg.role == "user"
    assert msg.text  # has text
    assert "exec_plan" in msg.text
    assert "step body" in msg.text


def test_create_compact_boundary_message_includes_trigger_and_tier() -> None:
    msg = create_compact_boundary_message(
        {"trigger": "auto", "tier": "microcompact"}
    )
    assert msg.role == "user"
    assert "auto" in msg.text
    assert "microcompact" in msg.text


def test_create_compact_boundary_message_handles_missing_metadata() -> None:
    msg = create_compact_boundary_message({})
    assert msg.role == "user"
    assert msg.text  # still produces a marker


def test_build_post_compact_messages_ordering() -> None:
    """Order: boundary, summary, keep, attachments."""
    summary = [ConversationMessage(role="user", content=[TextBlock(text="summary")])]
    keep = [ConversationMessage(role="assistant", content=[TextBlock(text="kept")])]
    atts = [CompactAttachment(kind="exec_plan", title="Plan", body="step")]
    boundary = ConversationMessage(role="user", content=[TextBlock(text="BOUND")])
    result = CompactionResult(
        trigger="auto",
        tier="full",
        boundary_marker=boundary,
        summary_messages=summary,
        messages_to_keep=keep,
        attachments=atts,
        metadata={},
    )
    out = build_post_compact_messages(result)
    assert out[0] is boundary
    assert out[1] is summary[0]
    assert out[2] is keep[0]
    # attachment rendered last
    assert "exec_plan" in out[-1].text


def test_build_post_compact_messages_with_no_attachments() -> None:
    boundary = ConversationMessage(role="user", content=[TextBlock(text="b")])
    result = CompactionResult(
        trigger="manual",
        tier="microcompact",
        boundary_marker=boundary,
        summary_messages=[],
        messages_to_keep=[],
        attachments=[],
        metadata={},
    )
    assert build_post_compact_messages(result) == [boundary]


def test_contract_fields_survive_end_to_end_rebuild() -> None:
    """The Spec 04 #5 contract: every listed field present after rebuild."""
    metadata = {
        "trigger": "auto",
        "tier": "full",
        "exec_plan_filename": "plans/p-001.md",
        "exec_plan_current_step": "step-3",
        "blocked_steps": [{"step_id": "s2", "reason": "waiting on review"}],
        "failing_tests": ["tests/test_x.py::test_a"],
        "modified_file_paths": ["src/a.py", "src/b.py"],
        "open_hooks": ["pre_commit"],
        "orientation_brief": "mission Y",
        "core_beliefs_digest": "atomic, reversible",
        "house_rules": "tests first; atomic writes only",
    }
    attachments = build_compact_attachments(metadata)
    result = CompactionResult(
        trigger="auto",
        tier="full",
        boundary_marker=create_compact_boundary_message(metadata),
        summary_messages=[],
        messages_to_keep=[],
        attachments=attachments,
        metadata=metadata,
    )
    rebuilt_text = "\n".join(m.text for m in build_post_compact_messages(result))
    for needle in (
        "plans/p-001.md",
        "step-3",
        "s2",
        "waiting on review",
        "tests/test_x.py::test_a",
        "src/a.py",
        "src/b.py",
        "pre_commit",
        "mission Y",
        "atomic, reversible",
        "tests first",
    ):
        assert needle in rebuilt_text, f"contract field {needle!r} missing from rebuild"
