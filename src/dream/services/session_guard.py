"""The combined session-start security gate (Spec 13E).

One entry point for everything that must pass before a session starts: the
spec-01 structural validator (``validate_repo`` — AGENTS.md, required tree,
JSON/schema, stale plans) plus the spec-13 Lurkr ``threat_scan`` (secret /
world_writable / eval_in_tool). The caller runs ``has_blocking`` over the
combined list and aborts before orientation if anything blocks.

Kept as its own module so it can depend on both ``repo_validator`` and
``threat_scan`` without either depending on the other (``threat_scan`` already
imports ``Finding`` from ``repo_validator``; a back-reference would be a cycle).
"""

from __future__ import annotations

from dream.config.paths import DreamPaths
from dream.services.repo_validator import Finding, validate_repo
from dream.services.threat_scan import threat_scan


def session_start_findings(paths: DreamPaths) -> list[Finding]:
    """All session-start findings: structural validation + the threat scan."""
    return validate_repo(paths) + threat_scan(paths)


__all__ = ["session_start_findings"]
