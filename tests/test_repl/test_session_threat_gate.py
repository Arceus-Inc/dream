"""Spec 13E.3 — the Lurkr threat scan gates a live REPL session start.

A worktree secret (or any blocking threat) aborts the session before the agent
runs, with the same "blocked" / exit-3 contract as the skill and MCP gates. A
clean worktree is not blocked by the scan (it proceeds to the env check).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dream.repl._session import run_session_repl

# AKIA + 16 chars, assembled so the literal isn't contiguous in this source.
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def test_secret_in_worktree_blocks_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(f'key = "{FAKE_AWS}"\n', encoding="utf-8")
    out = io.StringIO()
    code = run_session_repl(
        events_path=tmp_path / "ev.jsonl",
        working_dir=tmp_path,
        env={},  # env doesn't matter — the threat gate runs first
        output=out,
    )
    assert code == 3
    assert "blocked" in out.getvalue().lower()
    assert "redacted" in out.getvalue().lower()  # secret finding, value redacted


def test_clean_worktree_not_blocked_by_threat_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    out = io.StringIO()
    code = run_session_repl(
        events_path=tmp_path / "ev.jsonl",
        working_dir=tmp_path,
        env={},  # clean scan → not blocked; fails later on missing env (code 2)
        output=out,
    )
    # Exact code: a clean worktree passes the threat gate (not 3) and then fails
    # specifically at the missing-env check (2). Asserting != 3 would also pass
    # for an accidental success (0) or an unexpected failure, masking regressions.
    assert code == 2
