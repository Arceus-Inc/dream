"""Spec 04 stage 4c — reset trigger + structured handoff file.

When the compactor stops helping (two consecutive *unproductive* compactions,
or two consecutive compactor failures), the engine resets the session:
writes ``docs/sessions/handoff/{session-id}.md`` with five required
sections, emits ``context.handoff.written``, and seals the session as
``done-handed-off`` so the next session can pick up the thread.

This file tests the deterministic pieces — the counter logic and the
markdown writer — not the engine wiring (that's Spec 03).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.services.handoff import (
    HandoffSections,
    UnproductiveCompactionTracker,
    read_handoff,
    write_handoff,
)

# --- counter ----------------------------------------------------------------


def test_unproductive_counter_increments_when_compacted_without_progress() -> None:
    tracker = UnproductiveCompactionTracker()
    tracker.record_turn(compacted=True, productive=False)
    assert tracker.unproductive_count == 1
    assert tracker.should_reset() is False


def test_productive_turn_resets_counter() -> None:
    tracker = UnproductiveCompactionTracker(unproductive_count=1)
    tracker.record_turn(compacted=True, productive=True)
    assert tracker.unproductive_count == 0


def test_turn_without_compaction_does_not_change_counter() -> None:
    tracker = UnproductiveCompactionTracker(unproductive_count=1)
    tracker.record_turn(compacted=False, productive=False)
    assert tracker.unproductive_count == 1


def test_two_unproductive_compactions_trigger_reset() -> None:
    tracker = UnproductiveCompactionTracker()
    tracker.record_turn(compacted=True, productive=False)
    tracker.record_turn(compacted=True, productive=False)
    assert tracker.unproductive_count == 2
    assert tracker.should_reset() is True
    assert tracker.reset_reason() == "unproductive_compactions"


def test_two_compactor_failures_trigger_reset() -> None:
    tracker = UnproductiveCompactionTracker()
    tracker.record_compactor_failure()
    tracker.record_compactor_failure()
    assert tracker.compactor_failure_count == 2
    assert tracker.should_reset() is True
    assert tracker.reset_reason() == "compactor_failures"


def test_compactor_success_resets_failure_counter() -> None:
    tracker = UnproductiveCompactionTracker(compactor_failure_count=1)
    tracker.record_compactor_success()
    assert tracker.compactor_failure_count == 0


def test_productive_turn_does_not_clear_compactor_failures() -> None:
    """A productive turn signals ledger advancement, NOT compactor health.

    The compactor-failure counter is its own reset trigger per spec §9; only
    a successful compaction clears it.
    """
    tracker = UnproductiveCompactionTracker(compactor_failure_count=1)
    tracker.record_turn(compacted=False, productive=True)
    assert tracker.compactor_failure_count == 1


# --- HandoffSections / writer / reader --------------------------------------


def _sample_sections() -> HandoffSections:
    return HandoffSections(
        why="Two consecutive compactions did not unstick the ledger.",
        attempted="Tried steps A, B, C; B blocked on dep X.",
        still_open="step-3 still pending; failing tests: test_foo.",
        known_bad="Approach Y deadlocks under load.",
        next_action="Investigate dep X resolution; retry B.",
        sealed_jsonl_path="docs/sessions/log/s_abc.jsonl",
        final_checkpoint_ref="ckpt_42",
    )


def test_reset_writes_handoff_with_required_sections(tmp_path: Path) -> None:
    sections = _sample_sections()
    written = write_handoff(root=tmp_path, session_id="s_abc", sections=sections)

    assert written == tmp_path / "docs" / "sessions" / "handoff" / "s_abc.md"
    text = written.read_text(encoding="utf-8")
    assert "# Why handing off" in text
    assert "# What was attempted" in text
    assert "# What is still open" in text
    assert "# What is known not to work" in text
    assert "# Next concrete action" in text
    # Body content for each
    assert "Two consecutive compactions" in text
    assert "Tried steps A, B, C" in text
    assert "step-3 still pending" in text
    assert "Approach Y deadlocks" in text
    assert "Investigate dep X" in text
    # Pointers
    assert "docs/sessions/log/s_abc.jsonl" in text
    assert "ckpt_42" in text


def test_handoff_writer_is_atomic_no_temp_leftovers(tmp_path: Path) -> None:
    """write_handoff must use atomic_write_text — no .tmp.* siblings remain."""
    sections = _sample_sections()
    write_handoff(root=tmp_path, session_id="s_xyz", sections=sections)
    handoff_dir = tmp_path / "docs" / "sessions" / "handoff"
    leftovers = [p for p in handoff_dir.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_handoff_writer_creates_parent_directories(tmp_path: Path) -> None:
    sections = _sample_sections()
    # tmp_path is empty — no docs/ tree exists yet.
    write_handoff(root=tmp_path, session_id="s_brand_new", sections=sections)
    assert (tmp_path / "docs" / "sessions" / "handoff" / "s_brand_new.md").exists()


def test_next_session_orientation_reads_handoff(tmp_path: Path) -> None:
    """The orientation loader reaches into ``docs/sessions/handoff/`` by session id."""
    sections = _sample_sections()
    write_handoff(root=tmp_path, session_id="s_prev", sections=sections)
    text = read_handoff(root=tmp_path, session_id="s_prev")
    assert text is not None
    assert "Why handing off" in text


def test_read_handoff_returns_none_for_missing_session(tmp_path: Path) -> None:
    assert read_handoff(root=tmp_path, session_id="s_nope") is None


def test_handoff_session_id_rejects_path_traversal(tmp_path: Path) -> None:
    sections = _sample_sections()
    with pytest.raises(ValueError):
        write_handoff(root=tmp_path, session_id="../escape", sections=sections)
    with pytest.raises(ValueError):
        write_handoff(root=tmp_path, session_id="bad/slash", sections=sections)
