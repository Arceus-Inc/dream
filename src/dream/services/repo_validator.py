"""Session-start repo validator (spec 01).

Runs blocking/warning/info checks before a session starts: the ``AGENTS.md``
contract (presence, line caps, resolvable links), the required ``docs/`` tree
(present and not git-ignored — the repo, not the working tree, is the source of
truth), JSON well-formedness + ``$schema`` validation, and stale exec-plans. A
blocking finding means "do not start"; ``has_blocking`` is the gate.

Secret scanning lives in :mod:`dream.services.threat_scan` (Spec 13E), which
scans the whole worktree, not just ``docs/``; :mod:`dream.services.session_guard`
combines both into one session-start gate.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import jsonschema

from dream.config.paths import DreamPaths
from dream.utils.git import run_git

__all__ = ["Finding", "Severity", "has_blocking", "validate_repo"]

Severity = Literal["blocking", "warning", "info"]

_AGENTS_HARD_CAP = 300
_AGENTS_SOFT_CAP = 100
_STALE_EXEC_PLAN_DAYS = 7

# Required tree (relative to repo root). Folders may be empty; the repo is the
# source of truth, so a git-ignored required path counts as missing.
_REQUIRED_PATHS = (
    "docs/design-docs/core-beliefs.md",
    "docs/exec-plans/active",
    "docs/product-specs",
    "docs/references",
    "docs/SECURITY.md",
)

_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@dataclass(frozen=True)
class Finding:
    """A validator result. ``blocking`` stops the session; others are advisory."""

    severity: Severity
    code: str
    message: str
    path: str | None = None


def has_blocking(findings: list[Finding]) -> bool:
    """True if any finding would prevent the session from starting."""
    return any(f.severity == "blocking" for f in findings)


def validate_repo(paths: DreamPaths) -> list[Finding]:
    """Run every session-start check and return the collected findings."""
    findings: list[Finding] = []
    findings += _check_agents_md(paths)
    findings += _check_required_tree(paths)
    findings += _check_docs_json(paths)
    findings += _check_stale_exec_plans(paths)
    return findings


def _check_agents_md(paths: DreamPaths) -> list[Finding]:
    agents = paths.agents_md
    if not agents.is_file():
        return [Finding("blocking", "agents_md_missing", "AGENTS.md is missing", "AGENTS.md")]

    findings: list[Finding] = []
    text = agents.read_text(encoding="utf-8")  # read once; reused for link checks
    lines = text.splitlines()
    if len(lines) > _AGENTS_HARD_CAP:
        findings.append(
            Finding(
                "blocking",
                "agents_md_oversized",
                f"AGENTS.md has {len(lines)} lines (hard cap {_AGENTS_HARD_CAP})",
                "AGENTS.md",
            )
        )
    elif len(lines) > _AGENTS_SOFT_CAP:
        findings.append(
            Finding(
                "warning",
                "agents_md_soft_cap",
                f"AGENTS.md has {len(lines)} lines (soft cap {_AGENTS_SOFT_CAP})",
                "AGENTS.md",
            )
        )
    findings += _check_links(paths, text)
    return findings


def _check_links(paths: DreamPaths, content: str) -> list[Finding]:
    findings: list[Finding] = []
    for target in _MARKDOWN_LINK.findall(content):
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        rel = target.split("#", 1)[0]  # drop anchors
        if not rel:
            continue
        dest = (paths.repo / rel).resolve()
        if not dest.is_relative_to(paths.repo):
            # Absolute or ../ target escapes the repo: refuse without reading it.
            findings.append(
                Finding(
                    "blocking",
                    "agents_md_external_link",
                    f"AGENTS.md link escapes the repo: {rel}",
                    rel,
                )
            )
            continue
        if not dest.exists():
            findings.append(
                Finding(
                    "blocking",
                    "agents_md_dead_link",
                    f"AGENTS.md link does not resolve: {rel}",
                    rel,
                )
            )
        elif dest.is_file() and not dest.read_text(encoding="utf-8", errors="ignore").strip():
            findings.append(
                Finding(
                    "warning",
                    "agents_md_empty_link",
                    f"AGENTS.md links to an empty file: {rel}",
                    rel,
                )
            )
    return findings


def _check_required_tree(paths: DreamPaths) -> list[Finding]:
    findings: list[Finding] = []
    for rel in _REQUIRED_PATHS:
        if not (paths.repo / rel).exists():
            findings.append(
                Finding(
                    "blocking", "required_path_missing", f"required path is missing: {rel}", rel
                )
            )
            continue
        # A git-ignored required path counts as missing (repo is source of truth).
        # --no-index applies the ignore rules regardless of current tracking state.
        if run_git(["check-ignore", "--no-index", "-q", rel], cwd=paths.repo)[0] == 0:
            findings.append(
                Finding(
                    "blocking", "required_path_ignored", f"required path is git-ignored: {rel}", rel
                )
            )
    return findings


def _check_docs_json(paths: DreamPaths) -> list[Finding]:
    findings: list[Finding] = []
    if not paths.docs_dir.is_dir():
        return findings
    for jf in sorted(paths.docs_dir.rglob("*.json")):
        rel = str(jf.relative_to(paths.repo))
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(Finding("blocking", "invalid_json", f"malformed JSON: {exc}", rel))
            continue
        findings += _validate_against_schema(paths, jf, rel, data)
    return findings


def _validate_against_schema(paths: DreamPaths, jf: Path, rel: str, data: object) -> list[Finding]:
    if not isinstance(data, dict):
        return []
    schema_ref = data.get("$schema")
    if not isinstance(schema_ref, str) or "://" in schema_ref:
        return []  # no local schema declared (or a remote URI we don't fetch)
    schema_path = (jf.parent / schema_ref).resolve()
    if not schema_path.is_relative_to(paths.repo):
        # Refuse to load a schema from outside the repo (../ or absolute escape).
        return [
            Finding(
                "blocking", "schema_path_invalid", f"$schema escapes the repo: {schema_ref}", rel
            )
        ]
    if not schema_path.is_file():
        return [
            Finding("blocking", "schema_missing", f"$schema does not resolve: {schema_ref}", rel)
        ]
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)
    except (OSError, json.JSONDecodeError) as exc:
        return [
            Finding("blocking", "invalid_json", f"unreadable/malformed $schema file: {exc}", rel)
        ]
    except jsonschema.SchemaError as exc:
        return [
            Finding(
                "blocking", "invalid_schema", f"$schema is not a valid schema: {exc.message}", rel
            )
        ]
    except jsonschema.ValidationError as exc:
        return [
            Finding(
                "blocking", "schema_validation_failed", f"JSON fails $schema: {exc.message}", rel
            )
        ]
    return []


def _check_stale_exec_plans(paths: DreamPaths) -> list[Finding]:
    findings: list[Finding] = []
    active = paths.exec_plans_active
    if not active.is_dir():
        return findings
    cutoff = time.time() - _STALE_EXEC_PLAN_DAYS * 86400
    for f in sorted(active.rglob("*")):
        if f.is_file() and f.name != ".keep" and f.stat().st_mtime < cutoff:
            rel = str(f.relative_to(paths.repo))
            findings.append(
                Finding(
                    "warning",
                    "stale_exec_plan",
                    f"exec-plan untouched > {_STALE_EXEC_PLAN_DAYS}d",
                    rel,
                )
            )
    return findings
