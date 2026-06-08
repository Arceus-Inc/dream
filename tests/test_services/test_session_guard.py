"""Spec 13E.2 — the combined session-start security gate.

``session_start_findings`` merges the spec-01 structural validator
(``validate_repo``) with the spec-13 Lurkr ``threat_scan`` into one
``has_blocking`` decision.
"""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.services.repo_validator import has_blocking
from dream.services.session_guard import session_start_findings

FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def _paths(tmp_path: Path) -> DreamPaths:
    return DreamPaths.resolve(tmp_path, env={})


def test_combines_validator_and_threat_scan(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(f'key = "{FAKE_AWS}"\n', encoding="utf-8")
    findings = session_start_findings(_paths(tmp_path))
    codes = {f.code for f in findings}
    assert "agents_md_missing" in codes  # from validate_repo (no AGENTS.md)
    assert "secret" in codes  # from threat_scan
    assert has_blocking(findings)
