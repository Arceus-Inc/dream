"""Spec 13F.2 — standing orders from core-beliefs.md gate a live session's prompt.

The governance constitution is extracted at session start and prepended (first)
to the system prompt, so every session runs under the ALWAYS / NEVER rules.
"""

from __future__ import annotations

from pathlib import Path

from dream.repl._session import build_default_harness
from dream.session import SessionOptions


def _env() -> dict[str, str]:
    return {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_SMOKE_BASE_URL": "http://127.0.0.1:9/v1",
    }


def _write_core_beliefs(tmp_path: Path, text: str) -> None:
    d = tmp_path / "docs" / "design-docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "core-beliefs.md").write_text(text, encoding="utf-8")


def test_standing_orders_prepended_to_system_prompt(tmp_path: Path) -> None:
    _write_core_beliefs(
        tmp_path,
        "## Standing orders\n- always run tests\n## What we don't do\n- never push to main\n",
    )
    harness = build_default_harness(env=_env(), working_dir=tmp_path)
    engine = harness.config._engine_factory("sid", SessionOptions())
    system_prompt = engine.streamer._system_prompt  # type: ignore[attr-defined]
    assert "always run tests" in system_prompt
    assert "never push to main" in system_prompt
    # The constitution outranks everything: it's first in the prompt.
    assert system_prompt.startswith("# Standing orders (from core-beliefs.md")


def test_no_core_beliefs_means_no_block(tmp_path: Path) -> None:
    harness = build_default_harness(env=_env(), working_dir=tmp_path)
    engine = harness.config._engine_factory("sid", SessionOptions())
    system_prompt = engine.streamer._system_prompt  # type: ignore[attr-defined]
    assert "Standing orders (from core-beliefs.md" not in system_prompt
