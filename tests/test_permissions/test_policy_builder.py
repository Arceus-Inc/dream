"""Spec 13B — build_policy assembles a Policy from operator config.

The final integration: the assembled Policy feeds 13A's evaluate() so a trusted
built-in write tool is allowed while a discovered tool asks (the trust ramp).
"""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.permissions import (
    Outcome,
    PermissionRequest,
    SandboxTier,
    build_policy,
    evaluate,
)
from dream.utils.clock import FakeClock


def _paths(tmp_path: Path) -> DreamPaths:
    return DreamPaths.resolve(tmp_path, env={})


def _write(tmp_path: Path, name: str, content: str) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir(exist_ok=True)
    (harness / name).write_text(content, encoding="utf-8")


def test_defaults_when_no_config(tmp_path: Path) -> None:
    asm = build_policy(_paths(tmp_path), cwd=tmp_path, trusted_tiers={})
    assert asm.policy.tier is SandboxTier.REPO_WRITE
    assert asm.warnings == ()


def test_tier_flows_from_sandbox_config(tmp_path: Path) -> None:
    _write(tmp_path, "sandbox.toml", 'tier = "read-only"\n')
    asm = build_policy(_paths(tmp_path), cwd=tmp_path, trusted_tiers={})
    assert asm.policy.tier is SandboxTier.READ_ONLY


def test_required_tier_merges_trusted_and_overrides(tmp_path: Path) -> None:
    _write(tmp_path, "tool-tier-overrides.toml", '[ext]\ntier_required = "repo-write+net-allowlist"\n')
    asm = build_policy(
        _paths(tmp_path), cwd=tmp_path, trusted_tiers={"write": SandboxTier.REPO_WRITE}
    )
    assert asm.policy.required_tier["write"] is SandboxTier.REPO_WRITE
    assert asm.policy.required_tier["ext"] is SandboxTier.REPO_WRITE_NET


def test_override_wins_on_conflict(tmp_path: Path) -> None:
    _write(tmp_path, "tool-tier-overrides.toml", '[write]\ntier_required = "read-only"\n')
    asm = build_policy(
        _paths(tmp_path), cwd=tmp_path, trusted_tiers={"write": SandboxTier.REPO_WRITE}
    )
    assert asm.policy.required_tier["write"] is SandboxTier.READ_ONLY


def test_extras_threaded_and_anchored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "sandbox.toml",
        'extra_allowed = ["../shared"]\ncredential_extra = ["**/*.vault"]\n',
    )
    asm = build_policy(_paths(tmp_path), cwd=tmp_path, trusted_tiers={})
    assert asm.policy.credential_extra == ("**/*.vault",)
    assert asm.policy.extra_allowed == (tmp_path / "../shared",)


def test_tool_allow_passed_through(tmp_path: Path) -> None:
    asm = build_policy(
        _paths(tmp_path), cwd=tmp_path, trusted_tiers={}, tool_allow=frozenset({"x"})
    )
    assert asm.policy.tool_allow == frozenset({"x"})


def test_staleness_warnings_surfaced(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tool-tier-overrides.toml",
        '[x]\ntier_required = "repo-write"\npromoted_at = "2000-01-01T00:00:00Z"\n',
    )
    asm = build_policy(
        _paths(tmp_path), cwd=tmp_path, trusted_tiers={}, clock=FakeClock(start_ms=10**15)
    )
    assert len(asm.warnings) == 1


def test_built_policy_allows_trusted_write_but_asks_discovered(tmp_path: Path) -> None:
    asm = build_policy(
        _paths(tmp_path), cwd=tmp_path, trusted_tiers={"write": SandboxTier.REPO_WRITE}
    )
    target = (tmp_path / "f.txt",)
    allow = evaluate(
        PermissionRequest(tool_name="write", is_read_only=False, target_paths=target),
        asm.policy,
    )
    assert allow.outcome is Outcome.ALLOW
    ask = evaluate(
        PermissionRequest(tool_name="mcp_unknown", is_read_only=False, target_paths=target),
        asm.policy,
    )
    assert ask.outcome is Outcome.ASK
