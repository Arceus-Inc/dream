"""Boot gates + resume scan (spec 15 P1 §1, left-over spec 05).

The boot sequence mirrors the REPL gate order so a headless runtime gets
the same protections: skills gate (block) → structural validate (warn) →
threat scan (block) → resume scan (sidecars with in-flight tasks).
"""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.runtime._boot import run_boot_gates, scan_resume_candidates
from dream.state.sidecar import create_sidecar, update_state


def _paths(tmp_path: Path) -> DreamPaths:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return DreamPaths.resolve(repo, home=tmp_path / "home")


def test_clean_repo_passes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    report = run_boot_gates(working_dir=paths.repo, paths=paths)
    assert not report.blocked
    assert report.resume_candidates == ()


def test_malformed_skill_blocks(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    skill = paths.repo / "docs" / "skills" / "bad-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("no frontmatter at all\n", encoding="utf-8")
    report = run_boot_gates(working_dir=paths.repo, paths=paths)
    assert report.blocked
    assert any("bad-skill" in (f.path or "") for f in report.blocking_findings())


def test_repo_findings_are_advisory(tmp_path: Path) -> None:
    # An empty repo misses AGENTS.md / docs tree — warnings, never blocks.
    paths = _paths(tmp_path)
    report = run_boot_gates(working_dir=paths.repo, paths=paths)
    assert report.repo_findings  # structural validator found gaps
    assert not report.blocked


def test_resume_scan_finds_running_sidecars(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    create_sidecar(paths, "t-running", base_branch="main", harness_version="0.1.0")
    create_sidecar(paths, "t-done", base_branch="main", harness_version="0.1.0")
    update_state(paths, "t-done", status="completed")

    candidates = scan_resume_candidates(paths)
    assert [c.task_id for c in candidates] == ["t-running"]


def test_resume_scan_skips_corrupt_state(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    create_sidecar(paths, "t-ok", base_branch="main", harness_version="0.1.0")
    corrupt = paths.sidecars_dir / "t-corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "state.json").write_text("{not json", encoding="utf-8")

    report = run_boot_gates(working_dir=paths.repo, paths=paths)
    assert [c.task_id for c in report.resume_candidates] == ["t-ok"]
    assert report.corrupt_sidecars == ("t-corrupt",)
    assert not report.blocked


def test_resume_scan_empty_when_no_sidecars(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert scan_resume_candidates(paths) == ()
