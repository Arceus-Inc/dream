"""Spec 06.5 slice 1 — ``HEARTBEAT.md`` bundled default + operator override.

The wake runner takes its system prompt from ``.dream/HEARTBEAT.md`` if
present, otherwise from the bundled default shipped inside the package.
``HEARTBEAT.md`` is the background-turn system prompt — and ONLY the
background-turn system prompt; it is not mixed into the main session's
prompt stack.
"""

from __future__ import annotations

from pathlib import Path

from dream.wake import load_heartbeat_prompt
from dream.wake._prompt import BUNDLED_HEARTBEAT_PROMPT


def test_bundled_prompt_is_non_empty() -> None:
    assert isinstance(BUNDLED_HEARTBEAT_PROMPT, str)
    assert BUNDLED_HEARTBEAT_PROMPT.strip()


def test_bundled_prompt_mentions_the_heartbeat_tool() -> None:
    """The bundled default must steer the model toward the ``heartbeat`` tool.

    Without this nudge the model produces prose instead of a tool call and
    every wake decides ``missing`` — see ``test_runner.py``.
    """
    assert "heartbeat" in BUNDLED_HEARTBEAT_PROMPT.lower()


def test_load_returns_bundled_when_path_is_none() -> None:
    assert load_heartbeat_prompt(None) == BUNDLED_HEARTBEAT_PROMPT


def test_load_returns_bundled_when_path_missing(tmp_path: Path) -> None:
    assert load_heartbeat_prompt(tmp_path / "does-not-exist.md") == BUNDLED_HEARTBEAT_PROMPT


def test_load_returns_file_contents_when_path_exists(tmp_path: Path) -> None:
    override = tmp_path / "HEARTBEAT.md"
    override.write_text("custom wake prompt — call heartbeat", encoding="utf-8")
    assert load_heartbeat_prompt(override) == "custom wake prompt — call heartbeat"


def test_load_strips_no_content(tmp_path: Path) -> None:
    """An operator override is taken verbatim — no trim, no rewrap."""
    override = tmp_path / "HEARTBEAT.md"
    override.write_text("  leading + trailing whitespace  \n", encoding="utf-8")
    assert load_heartbeat_prompt(override) == "  leading + trailing whitespace  \n"
