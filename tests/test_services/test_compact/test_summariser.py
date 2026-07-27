"""Compaction summariser + TODO reinject (P0 #4 Wave A)."""

from __future__ import annotations

from pathlib import Path

from dream.engine._messages import ConversationMessage, TextBlock, ToolUseBlock
from dream.services.compact._carryover_state import CarryoverMetadata
from dream.services.compact._summariser import (
    inject_todo_snapshot,
    make_deterministic_summariser,
    parse_todo_pending,
    render_transcript_excerpt,
)


def test_render_transcript_excerpt_includes_tool_use_input() -> None:
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id="tu_1", name="bash", input={"command": "pytest -q"}),
            ],
        ),
    ]
    excerpt = render_transcript_excerpt(messages)
    assert "bash" in excerpt
    assert "pytest -q" in excerpt


def test_parse_todo_pending_tolerates_read_errors(tmp_path: Path) -> None:
    todo = tmp_path / "TODO.md"
    todo.write_bytes(b"- [ ] bad \xff utf8\n")
    assert parse_todo_pending(tmp_path) == []


def test_parse_todo_pending_tolerates_missing_workspace(tmp_path: Path) -> None:
    assert parse_todo_pending(tmp_path / "missing") == []


def test_render_transcript_excerpt_flattens_roles() -> None:
    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="hello")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
    ]
    excerpt = render_transcript_excerpt(messages)
    assert "USER: hello" in excerpt
    assert "ASSISTANT: world" in excerpt


def test_deterministic_summariser_rolls_previous_summary() -> None:
    state = CarryoverMetadata(previous_summary="prior goal")
    summariser = make_deterministic_summariser(state)
    older = [ConversationMessage(role="user", content=[TextBlock(text="did thing")])]
    out = summariser(older)
    assert len(out) == 1
    assert "[Compaction summary" in out[0].text
    assert state.previous_summary is not None
    assert "did thing" in state.previous_summary


def test_parse_todo_pending_reads_unchecked_items(tmp_path: Path) -> None:
    (tmp_path / "TODO.md").write_text(
        "# TODO\n- [ ] alpha\n- [x] done\n- [ ] beta\n",
        encoding="utf-8",
    )
    assert parse_todo_pending(tmp_path) == ["alpha", "beta"]


def test_inject_todo_snapshot_appends_user_message(tmp_path: Path) -> None:
    (tmp_path / "TODO.md").write_text("# TODO\n- [ ] ship it\n", encoding="utf-8")
    base = [
        ConversationMessage(role="user", content=[TextBlock(text="boundary")]),
    ]
    out = inject_todo_snapshot(base, tmp_path)
    assert len(out) == 2
    assert "ship it" in out[-1].text
    assert "TODO snapshot" in out[-1].text


def test_inject_todo_snapshot_noop_when_empty(tmp_path: Path) -> None:
    base = [ConversationMessage(role="user", content=[TextBlock(text="x")])]
    assert inject_todo_snapshot(base, tmp_path) is base
