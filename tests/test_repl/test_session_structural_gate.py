"""PR#49 follow-up — structural validation surfaces at REPL session start.

The spec-01 structural validator (``validate_repo``: AGENTS.md, required docs
tree, JSON/schema, stale exec-plans) runs at session start, but unlike the
Lurkr threat scan it is *advisory*: its findings are printed as warnings and
the session still proceeds. Blocking the live REPL on the harness's own docs
layout would break embedded-SDK consumers whose repos don't carry it, so only
security findings (threat_scan) abort the start. The point of wiring it is that
the findings are no longer silently skipped.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dream.repl._session import run_session_repl

# AKIA + 16 chars, assembled so the literal isn't contiguous in this source.
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def test_missing_structure_warns_but_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))
    # A clean (no-secret) worktree with no AGENTS.md and no docs tree.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    out = io.StringIO()
    code = run_session_repl(
        events_path=tmp_path / "ev.jsonl",
        working_dir=tmp_path,
        env={},  # clean scan → structural warns, then fails on missing env (2)
        output=out,
    )
    text = out.getvalue()
    # Structural validator surfaced (no longer silently skipped)...
    assert "warning" in text.lower()
    assert "AGENTS.md" in text
    # ...but it did NOT block: the start proceeds to the missing-env check (2),
    # not the blocked/exit-3 path.
    assert code == 2
    assert "blocked" not in text.lower()


def test_threat_still_blocks_even_when_structure_only_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))
    # No AGENTS.md (structural would warn) AND a worktree secret (threat blocks).
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(f'key = "{FAKE_AWS}"\n', encoding="utf-8")
    out = io.StringIO()
    code = run_session_repl(
        events_path=tmp_path / "ev.jsonl",
        working_dir=tmp_path,
        env={},
        output=out,
    )
    text = out.getvalue()
    assert code == 3
    assert "blocked" in text.lower()
