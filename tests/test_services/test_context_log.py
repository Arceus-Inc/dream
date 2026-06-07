"""Spec 04 stage 4a — typed jsonl event log for context operations.

Every context operation (compaction trigger/completion, reset, handoff,
tool-output offload, skill load) is logged as a typed jsonl event. The
agent itself can later read this log back (the basis for spec 04's
``read_my_context_log``) so it can prefer compactable content rather
than re-discovering what's already happened to its own window.

Two surfaces:

- A typed ``ContextEvent`` union, each a frozen dataclass carrying a
  stable string ``name`` and the event payload. Serialised one-per-line
  via ``to_jsonl_line`` / ``from_jsonl_line``.
- ``ContextLogWriter`` (append-only file sink) and ``read_context_log``
  (parser). Both are pure file I/O — no engine wiring lives here.

The event catalogue is fixed by Spec 04's `## Artefact shapes` section.
A new event type means a new dataclass — never a free-form ``extra``
field — so consumers can branch on ``isinstance``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.services.context_log import (
    ContextCompactionCompleted,
    ContextCompactionTriggered,
    ContextEvent,
    ContextHandoffWritten,
    ContextLogWriter,
    ContextResetTriggered,
    ContextSkillLoaded,
    ContextToolOutputOffloaded,
    from_jsonl_line,
    read_context_log,
    to_jsonl_line,
)

# --- event names are stable strings ------------------------------------------


def test_event_names_are_spec_catalogue() -> None:
    """Spec 04 `## Artefact shapes` pins these names; renaming MUST break this test."""
    assert ContextCompactionTriggered.name == "context.compaction.triggered"
    assert ContextCompactionCompleted.name == "context.compaction.completed"
    assert ContextResetTriggered.name == "context.reset.triggered"
    assert ContextHandoffWritten.name == "context.handoff.written"
    assert ContextToolOutputOffloaded.name == "context.tool_output.offloaded"
    assert ContextSkillLoaded.name == "context.skill.loaded"


# --- event payload shapes ---------------------------------------------------


def test_compaction_triggered_carries_utilisation_and_trigger() -> None:
    ev = ContextCompactionTriggered(utilisation=0.72, trigger="auto")
    assert ev.utilisation == pytest.approx(0.72)
    assert ev.trigger == "auto"


def test_compaction_completed_carries_tier_and_post_utilisation() -> None:
    ev = ContextCompactionCompleted(
        tier="microcompact",
        preserved_attachments=4,
        resulting_utilisation=0.51,
    )
    assert ev.tier == "microcompact"
    assert ev.preserved_attachments == 4
    assert ev.resulting_utilisation == pytest.approx(0.51)


def test_reset_triggered_carries_reason() -> None:
    ev = ContextResetTriggered(reason="two-unproductive-compactions")
    assert ev.reason == "two-unproductive-compactions"


def test_handoff_written_carries_path() -> None:
    ev = ContextHandoffWritten(path="docs/sessions/handoff/abc.md")
    assert ev.path == "docs/sessions/handoff/abc.md"


def test_tool_output_offloaded_carries_offload_metadata() -> None:
    ev = ContextToolOutputOffloaded(
        tool_name="bash",
        tool_use_id="tu_1",
        offloaded_to="20240101-bash-deadbeef.txt",
        original_size_bytes=12_345,
    )
    assert ev.tool_name == "bash"
    assert ev.tool_use_id == "tu_1"
    assert ev.offloaded_to == "20240101-bash-deadbeef.txt"
    assert ev.original_size_bytes == 12_345


def test_skill_loaded_carries_skill_name() -> None:
    ev = ContextSkillLoaded(skill_name="azure-prepare")
    assert ev.skill_name == "azure-prepare"


# --- frozen dataclasses ------------------------------------------------------


def test_events_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    ev = ContextCompactionTriggered(utilisation=0.5, trigger="auto")
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(ev, "trigger", "manual")


# --- jsonl roundtrip ---------------------------------------------------------


def _all_events() -> list[ContextEvent]:
    return [
        ContextCompactionTriggered(utilisation=0.72, trigger="auto"),
        ContextCompactionTriggered(utilisation=0.99, trigger="reactive"),
        ContextCompactionCompleted(
            tier="full",
            preserved_attachments=6,
            resulting_utilisation=0.34,
        ),
        ContextResetTriggered(reason="two-compactor-failures"),
        ContextHandoffWritten(path="docs/sessions/handoff/abc.md"),
        ContextToolOutputOffloaded(
            tool_name="bash",
            tool_use_id="tu_99",
            offloaded_to="20240101-bash-deadbeef.txt",
            original_size_bytes=12_345,
        ),
        ContextSkillLoaded(skill_name="azure-prepare"),
    ]


@pytest.mark.parametrize("event", _all_events())
def test_to_jsonl_line_then_from_jsonl_line_roundtrips(event: ContextEvent) -> None:
    line = to_jsonl_line(event)
    assert "\n" not in line  # jsonl: one line per event
    parsed = json.loads(line)
    assert parsed["name"] == event.name
    assert from_jsonl_line(line) == event


def test_from_jsonl_line_rejects_unknown_event() -> None:
    with pytest.raises(ValueError):
        from_jsonl_line('{"name": "context.totally.made.up", "x": 1}')


def test_from_jsonl_line_rejects_malformed_json() -> None:
    with pytest.raises(ValueError):
        from_jsonl_line("not json at all")


def test_from_jsonl_line_rejects_non_string_name() -> None:
    """A non-string 'name' MUST raise ValueError, not an unhandled TypeError."""
    with pytest.raises(ValueError):
        from_jsonl_line('{"name": []}')


def test_from_jsonl_line_rejects_missing_required_field() -> None:
    """A known event missing a required field surfaces as ValueError uniformly."""
    # context.skill.loaded requires skill_name; omit it.
    with pytest.raises(ValueError):
        from_jsonl_line('{"name": "context.skill.loaded"}')


def test_read_context_log_surfaces_missing_field_as_valueerror(tmp_path: Path) -> None:
    """read_context_log callers see ValueError (not TypeError) for malformed lines."""
    path = tmp_path / "context.jsonl"
    path.write_text('{"name": "context.skill.loaded"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_context_log(path)


def test_to_jsonl_line_includes_iso_timestamp_when_present() -> None:
    when = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC).isoformat()
    line = to_jsonl_line(ContextSkillLoaded(skill_name="x", at=when))
    parsed = json.loads(line)
    assert parsed["at"] == when


# --- ContextLogWriter --------------------------------------------------------


def test_writer_appends_one_event_per_line(tmp_path: Path) -> None:
    path = tmp_path / "context.jsonl"
    writer = ContextLogWriter(path)
    writer.emit(ContextSkillLoaded(skill_name="a"))
    writer.emit(ContextSkillLoaded(skill_name="b"))
    writer.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["skill_name"] == "a"
    assert json.loads(lines[1])["skill_name"] == "b"


def test_writer_appends_when_file_already_exists(tmp_path: Path) -> None:
    """A re-opened log MUST extend rather than overwrite — sessions resume."""
    path = tmp_path / "context.jsonl"
    w1 = ContextLogWriter(path)
    try:
        w1.emit(ContextSkillLoaded(skill_name="first"))
    finally:
        w1.close()

    w2 = ContextLogWriter(path)
    try:
        w2.emit(ContextSkillLoaded(skill_name="second"))
    finally:
        w2.close()

    events = read_context_log(path)
    # Assert the FULL event list — filtering by type would let stray appended
    # events slip through undetected.
    assert events == [
        ContextSkillLoaded(skill_name="first"),
        ContextSkillLoaded(skill_name="second"),
    ]


def test_writer_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "context.jsonl"
    writer = ContextLogWriter(path)
    writer.emit(ContextSkillLoaded(skill_name="x"))
    writer.close()
    assert path.exists()


def test_writer_flushes_each_emit(tmp_path: Path) -> None:
    """Crash-resume relies on every emitted event being on disk immediately."""
    path = tmp_path / "context.jsonl"
    writer = ContextLogWriter(path)
    writer.emit(ContextSkillLoaded(skill_name="x"))
    # Without close(), the line MUST already be visible to a parallel reader.
    assert path.read_text(encoding="utf-8").strip() != ""
    writer.close()


# --- read_context_log -------------------------------------------------------


def test_read_returns_empty_for_missing_file(tmp_path: Path) -> None:
    """A missing log is not an error — the session simply hasn't emitted anything."""
    assert read_context_log(tmp_path / "nope.jsonl") == []


def test_read_returns_events_in_order(tmp_path: Path) -> None:
    path = tmp_path / "context.jsonl"
    writer = ContextLogWriter(path)
    expected = _all_events()
    for ev in expected:
        writer.emit(ev)
    writer.close()
    assert read_context_log(path) == expected


def test_read_skips_blank_lines(tmp_path: Path) -> None:
    """Trailing newlines from crashed writers MUST not break a re-read."""
    path = tmp_path / "context.jsonl"
    path.write_text(
        "\n".join(
            [
                "",
                to_jsonl_line(ContextSkillLoaded(skill_name="a")),
                "",
                to_jsonl_line(ContextSkillLoaded(skill_name="b")),
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert read_context_log(path) == [
        ContextSkillLoaded(skill_name="a"),
        ContextSkillLoaded(skill_name="b"),
    ]


def test_read_raises_on_malformed_line(tmp_path: Path) -> None:
    """A corrupted log line MUST raise — silent dropping would hide bugs."""
    path = tmp_path / "context.jsonl"
    path.write_text(
        to_jsonl_line(ContextSkillLoaded(skill_name="ok")) + "\n"
        + "this is not json\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_context_log(path)
