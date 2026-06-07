"""Spec 06 Slice 2 — REPL skill surface + session-start wiring."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dream.repl._events import EventSink
from dream.repl._session import (
    _cmd_skill,
    _cmd_skills,
    build_default_harness,
    run_session_repl,
)
from dream.session import SessionOptions
from dream.skills import SKILL_CONTEXT_KEY, build_session_skill_registry
from dream.skills._frontmatter import read_skill_meta
from dream.skills._registry import SkillRegistry
from tests.test_skills._helpers import write_skill


def _registry_with(path: Path) -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(read_skill_meta(path, source="project"))
    return reg


# --- /skills and /skill helpers ---------------------------------------------


def test_cmd_skills_lists_frontmatter(tmp_path: Path) -> None:
    reg = _registry_with(write_skill(tmp_path, "refactor"))
    out = io.StringIO()
    _cmd_skills(reg, output=out, use=False)
    text = out.getvalue()
    assert "refactor" in text
    assert "project" in text


def test_cmd_skills_empty(tmp_path: Path) -> None:
    out = io.StringIO()
    _cmd_skills(None, output=out, use=False)
    assert "no skills" in out.getvalue()


def test_cmd_skill_operator_loads_user_only_skill(tmp_path: Path) -> None:
    """The operator may load a disable_model_invocation skill (the model can't)."""
    path = write_skill(
        tmp_path,
        "release",
        extra_frontmatter="disable_model_invocation: true",
        body="RELEASE STEPS HERE",
    )
    reg = _registry_with(path)
    out = io.StringIO()
    _cmd_skill("release", reg, sink=EventSink(tmp_path / "ev.jsonl"), output=out, use=False)
    assert "RELEASE STEPS HERE" in out.getvalue()


def test_cmd_skill_unknown(tmp_path: Path) -> None:
    out = io.StringIO()
    _cmd_skill("ghost", SkillRegistry(), sink=EventSink(tmp_path / "ev.jsonl"), output=out, use=False)
    assert "unknown" in out.getvalue()


# --- session-start validation gate (MUST #3) --------------------------------


def test_malformed_skill_blocks_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))  # isolate user skills dir
    write_skill(tmp_path / "docs" / "skills", "bad", raw="not valid frontmatter")
    out = io.StringIO()
    code = run_session_repl(
        events_path=tmp_path / "ev.jsonl",
        working_dir=tmp_path,
        env={},  # no model env, but validation runs first
        output=out,
    )
    assert code == 3
    assert "blocked" in out.getvalue().lower()


# --- harness wiring: SkillContext + catalogue -------------------------------


async def test_harness_wires_skill_context_and_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))
    write_skill(tmp_path / "docs" / "skills", "refactor", description="Tidy the code.")
    registry, _ = build_session_skill_registry(tmp_path)
    harness = build_default_harness(
        env={"DREAM_SMOKE_API_KEY": "k", "DREAM_SMOKE_MODEL": "m"},
        working_dir=tmp_path,
        skill_registry=registry,
    )
    async with harness:
        session = await harness.start_session(SessionOptions())
        engine = session._engine
        assert engine is not None
        skill_ctx = engine.dispatcher.context_metadata[SKILL_CONTEXT_KEY]
        assert skill_ctx.registry is registry
        assert "skill" in skill_ctx.available_tools
        # catalogue surfaced into the model's system prompt
        assert "refactor" in (engine.streamer._system_prompt or "")
