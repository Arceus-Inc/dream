"""Spec 06 Slice 2 — session-start skill validation (MUST #3, fail fast)."""

from __future__ import annotations

from pathlib import Path

from dream.services.repo_validator import has_blocking
from dream.skills._validate import validate_skills
from tests.test_skills._helpers import write_skill


def test_valid_skills_yield_no_findings(tmp_path: Path) -> None:
    write_skill(tmp_path / "docs" / "skills", "ok")
    assert validate_skills(tmp_path, home=tmp_path / "home") == []


def test_missing_required_key_blocks(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "docs" / "skills",
        "bad",
        raw="---\nname: bad\nwhen_to_use: x\n---\nbody",  # no description
    )
    findings = validate_skills(tmp_path, home=tmp_path / "home")
    assert has_blocking(findings)
    assert any("bad" in (f.path or "") for f in findings)


def test_no_fences_blocks(tmp_path: Path) -> None:
    write_skill(tmp_path / "docs" / "skills", "bad2", raw="no frontmatter at all")
    assert has_blocking(validate_skills(tmp_path, home=tmp_path / "home"))


def test_project_skills_skipped_when_gated(tmp_path: Path) -> None:
    write_skill(tmp_path / "docs" / "skills", "bad", raw="not valid frontmatter")
    findings = validate_skills(
        tmp_path, home=tmp_path / "home", allow_project_skills=False
    )
    assert findings == []  # project source not scanned when gated off
