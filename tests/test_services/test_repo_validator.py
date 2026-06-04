"""Spec 01 — session-start validator.

Refuses to start on a broken repo (missing/oversized AGENTS.md, dead links,
incomplete required tree, malformed/invalid-against-schema JSON, leaked secrets,
git-ignored required folders) and surfaces soft issues as warnings. Secret
findings never echo the secret value.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.services.repo_validator import Finding, has_blocking, validate_repo

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # canonical example AWS key shape


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _codes(findings: list[Finding], severity: str) -> set[str]:
    return {f.code for f in findings if f.severity == severity}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".dream/\n")
    (r / "AGENTS.md").write_text(
        "# myrepo\n\nWhat this repo is.\n\nSee [beliefs](docs/design-docs/core-beliefs.md).\n"
    )
    (r / "docs" / "design-docs").mkdir(parents=True)
    (r / "docs" / "design-docs" / "core-beliefs.md").write_text("# Core beliefs\n\nWe ship.\n")
    for sub in ("exec-plans/active", "product-specs", "references"):
        d = r / "docs" / sub
        d.mkdir(parents=True)
        (d / ".keep").write_text("")
    (r / "docs" / "SECURITY.md").write_text("# Security\n\nReport to security@.\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    return r


@pytest.fixture
def paths(repo: Path, tmp_path: Path) -> DreamPaths:
    return DreamPaths.resolve(repo, home=tmp_path / "home", env={})


def test_valid_repo_has_no_blocking(paths: DreamPaths) -> None:
    assert not has_blocking(validate_repo(paths))


def test_missing_agents_md_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "AGENTS.md").unlink()
    assert "agents_md_missing" in _codes(validate_repo(paths), "blocking")


def test_oversized_agents_md_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(301)))
    assert "agents_md_oversized" in _codes(validate_repo(paths), "blocking")


def test_soft_cap_warns_not_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(150)))
    findings = validate_repo(paths)
    assert "agents_md_soft_cap" in _codes(findings, "warning")
    assert "agents_md_oversized" not in _codes(findings, "blocking")


def test_dead_link_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "AGENTS.md").write_text("# r\n\nSee [x](docs/nope.md).\n")
    assert "agents_md_dead_link" in _codes(validate_repo(paths), "blocking")


def test_link_to_empty_file_warns(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "empty.md").write_text("")
    (repo / "AGENTS.md").write_text("# r\n\nSee [e](docs/empty.md).\n")
    assert "agents_md_empty_link" in _codes(validate_repo(paths), "warning")


def test_required_path_missing_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "SECURITY.md").unlink()
    assert "required_path_missing" in _codes(validate_repo(paths), "blocking")


def test_git_ignored_required_folder_treated_missing(repo: Path, paths: DreamPaths) -> None:
    (repo / ".gitignore").write_text(".dream/\ndocs/references/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore references")
    assert "required_path_ignored" in _codes(validate_repo(paths), "blocking")


def test_invalid_json_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "bad.json").write_text("{not json")
    assert "invalid_json" in _codes(validate_repo(paths), "blocking")


def test_json_schema_violation_blocks(repo: Path, paths: DreamPaths) -> None:
    schemas = repo / "docs" / "_schemas"
    schemas.mkdir()
    (schemas / "item.schema.json").write_text(
        json.dumps(
            {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
        )
    )
    (repo / "docs" / "item.json").write_text(
        json.dumps({"$schema": "_schemas/item.schema.json", "wrong": 1})
    )
    assert "schema_validation_failed" in _codes(validate_repo(paths), "blocking")


def test_valid_json_with_schema_passes(repo: Path, paths: DreamPaths) -> None:
    schemas = repo / "docs" / "_schemas"
    schemas.mkdir()
    (schemas / "item.schema.json").write_text(
        json.dumps(
            {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
        )
    )
    (repo / "docs" / "item.json").write_text(
        json.dumps({"$schema": "_schemas/item.schema.json", "name": "ok"})
    )
    assert not has_blocking(validate_repo(paths))


def test_schema_missing_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "item.json").write_text(
        json.dumps({"$schema": "_schemas/nope.schema.json", "name": "x"})
    )
    assert "schema_missing" in _codes(validate_repo(paths), "blocking")


def test_external_and_anchor_links_are_skipped(repo: Path, paths: DreamPaths) -> None:
    (repo / "AGENTS.md").write_text(
        "# r\n\n[web](https://example.com) [a](#section) [m](mailto:x@y.z)\n"
    )
    findings = validate_repo(paths)
    assert "agents_md_dead_link" not in _codes(findings, "blocking")


def test_json_without_schema_key_is_allowed(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "plain.json").write_text(json.dumps({"any": "data"}))
    assert not has_blocking(validate_repo(paths))


def test_secret_in_docs_blocks(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "leak.md").write_text(f"key: {AWS_KEY}\n")
    assert "secret_detected" in _codes(validate_repo(paths), "blocking")


def test_secret_finding_redacts_value(repo: Path, paths: DreamPaths) -> None:
    (repo / "docs" / "leak.md").write_text(f"key: {AWS_KEY}\n")
    findings = validate_repo(paths)
    for f in findings:
        assert AWS_KEY not in f.message
        assert AWS_KEY not in (f.path or "")


def test_stale_exec_plan_warns(repo: Path, paths: DreamPaths) -> None:
    plan = repo / "docs" / "exec-plans" / "active" / "old.json"
    plan.write_text("{}")
    old = time.time() - 10 * 86400
    import os

    os.utime(plan, (old, old))
    assert "stale_exec_plan" in _codes(validate_repo(paths), "warning")


def test_has_blocking_helper() -> None:
    assert has_blocking([Finding("blocking", "c", "m")])
    assert not has_blocking([Finding("warning", "c", "m"), Finding("info", "c", "m")])
