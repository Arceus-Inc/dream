"""build_harness(orientation=True) wires an orientation that surfaces AGENTS.md (spec 15 §4.2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from dream._factory import _build_orientation_config, build_harness
from dream.config.paths import DreamPaths


def test_orientation_gather_reads_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md\nhello-contract\n", encoding="utf-8")
    cfg = _build_orientation_config(DreamPaths.resolve(tmp_path))
    brief = asyncio.run(cfg.gather())
    assert "hello-contract" in brief.repo_summary
    # the chorus deliverable repo is not a dream repo — no blocking findings / abort
    assert brief.validator_findings == ()
    assert not brief.has_blocking_findings


def test_orientation_gather_tolerates_missing_agents_md(tmp_path: Path) -> None:
    cfg = _build_orientation_config(DreamPaths.resolve(tmp_path))
    brief = asyncio.run(cfg.gather())
    assert brief.repo_summary == ""


def test_build_harness_orientation_is_opt_in_and_off_by_default(tmp_path: Path) -> None:
    # default: orientation off → session config carries no orientation (byte-identical legacy behaviour).
    off = build_harness(model="m", api_key="k", working_dir=tmp_path)
    off_engine = off.config._engine_factory("s1", _opts())
    assert off_engine.orientation is None
    # opt-in: orientation on → the engine's session config carries the AGENTS.md orientation.
    on = build_harness(model="m", api_key="k", working_dir=tmp_path, orientation=True)
    on_engine = on.config._engine_factory("s2", _opts())
    assert on_engine.orientation is not None


def _opts() -> object:
    from dream.session import SessionOptions

    return SessionOptions()
