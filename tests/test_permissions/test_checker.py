"""Spec 13A — the ordered permission-decision pipeline.

A table over every pipeline branch, plus the security invariants: the credential
guard beats any allow-list or tier, command-deny survives the unrestricted tier,
session-tier limits DENY while trust limits ASK, and evaluate is deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dream.permissions import (
    Outcome,
    PathRule,
    PermissionRequest,
    Policy,
    SandboxTier,
    evaluate,
)


def _req(**kw: Any) -> PermissionRequest:
    base: dict[str, Any] = {"tool_name": "t", "is_read_only": False}
    base.update(kw)
    return PermissionRequest(**base)


def test_credential_guard_beats_allow_list_and_unrestricted(tmp_path: Path) -> None:
    cred = Path.home() / ".ssh" / "id_rsa"
    pol = Policy(tier=SandboxTier.UNRESTRICTED, cwd=tmp_path, tool_allow=frozenset({"t"}))
    d = evaluate(_req(is_read_only=True, target_paths=(cred,)), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "credential_guard"


def test_tool_deny(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.REPO_WRITE, cwd=tmp_path, tool_deny=frozenset({"t"}))
    d = evaluate(_req(is_read_only=True), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "tool_deny"


def test_tool_allow_overrides_tool_deny_only(tmp_path: Path) -> None:
    # Allow-listing a deny-listed tool clears the tool-deny gate, but the rest of
    # the pipeline still runs: a read-only action then succeeds on its own merits.
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        tool_deny=frozenset({"t"}),
        tool_allow=frozenset({"t"}),
    )
    d = evaluate(_req(is_read_only=True), pol)
    assert d.outcome is Outcome.ALLOW
    assert d.rule == "read_only"


def test_tool_allow_does_not_bypass_command_deny(tmp_path: Path) -> None:
    # An allow-listed tool running a deny-pattern command is still denied.
    pol = Policy(tier=SandboxTier.UNRESTRICTED, cwd=tmp_path, tool_allow=frozenset({"t"}))
    d = evaluate(_req(is_read_only=True, command="rm -rf /"), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "command_deny"


def test_tool_allow_does_not_bypass_path_deny(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.UNRESTRICTED,
        cwd=tmp_path,
        tool_allow=frozenset({"t"}),
        path_deny=(PathRule(pattern="**/secret/**", allow=False),),
    )
    d = evaluate(_req(is_read_only=True, target_paths=(tmp_path / "secret" / "k.txt",)), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "path_deny"


def test_tool_allow_effectful_still_gated_by_trust(tmp_path: Path) -> None:
    # An untrusted but allow-listed tool doing a write is gated (ASK), not allowed.
    pol = Policy(tier=SandboxTier.REPO_WRITE, cwd=tmp_path, tool_allow=frozenset({"t"}))
    d = evaluate(_req(target_paths=(tmp_path / "x.txt",)), pol)
    assert d.outcome is Outcome.ASK
    assert d.rule == "tier_trust"


def test_path_deny_rule(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        path_deny=(PathRule(pattern="**/secret/**", allow=False),),
        required_tier={"t": SandboxTier.REPO_WRITE},
    )
    d = evaluate(_req(target_paths=(tmp_path / "secret" / "k.txt",)), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "path_deny"


def test_path_deny_matches_symlink_resolved_form(tmp_path: Path) -> None:
    # A symlink whose own path is innocuous but resolves into a denied location
    # must still be denied (no lexical-only bypass).
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "k.txt").write_text("x")
    link = tmp_path / "innocent.txt"
    link.symlink_to(secret_dir / "k.txt")
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        path_deny=(PathRule(pattern="**/secret/**", allow=False),),
    )
    d = evaluate(_req(is_read_only=True, target_paths=(link,)), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "path_deny"


def test_builtin_command_deny_survives_unrestricted(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.UNRESTRICTED, cwd=tmp_path)
    d = evaluate(_req(is_read_only=True, command="rm -rf /"), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "command_deny"


def test_operator_command_deny(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        command_deny=(re.compile(r"\bsecretctl\b"),),
    )
    d = evaluate(_req(is_read_only=True, command="secretctl dump"), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "command_deny"


def test_unrestricted_allows_write(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.UNRESTRICTED, cwd=tmp_path)
    d = evaluate(_req(target_paths=(Path("/etc/anywhere.conf"),)), pol)
    assert d.outcome is Outcome.ALLOW
    assert d.rule == "tier_unrestricted"


def test_read_only_action_allowed_at_any_tier(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.READ_ONLY, cwd=tmp_path)
    d = evaluate(_req(is_read_only=True, target_paths=(tmp_path / "main.py",)), pol)
    assert d.outcome is Outcome.ALLOW
    assert d.rule == "read_only"


def test_write_denied_at_read_only_session_tier(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.READ_ONLY,
        cwd=tmp_path,
        required_tier={"t": SandboxTier.REPO_WRITE},
    )
    d = evaluate(_req(target_paths=(tmp_path / "f.txt",)), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "tier_session"


def test_promoted_write_in_bounds_allowed(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"t": SandboxTier.REPO_WRITE},
    )
    d = evaluate(_req(target_paths=(tmp_path / "f.txt",)), pol)
    assert d.outcome is Outcome.ALLOW
    assert d.rule == "tier_grant"


def test_promoted_write_out_of_bounds_denied(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"t": SandboxTier.REPO_WRITE},
    )
    d = evaluate(_req(target_paths=(tmp_path.parent / "outside.txt",)), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "path_boundary"


def test_unpromoted_write_asks(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.REPO_WRITE, cwd=tmp_path)
    d = evaluate(_req(target_paths=(tmp_path / "f.txt",)), pol)
    assert d.outcome is Outcome.ASK
    assert d.rule == "tier_trust"


def test_network_denied_without_net_tier(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"t": SandboxTier.REPO_WRITE_NET},
    )
    d = evaluate(_req(is_read_only=True, network_host="api.example.com"), pol)
    assert d.outcome is Outcome.DENY
    assert d.rule == "tier_session"


def test_network_allowed_at_net_tier_when_trusted(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE_NET,
        cwd=tmp_path,
        required_tier={"t": SandboxTier.REPO_WRITE_NET},
    )
    d = evaluate(_req(is_read_only=True, network_host="api.example.com"), pol)
    assert d.outcome is Outcome.ALLOW
    assert d.rule == "tier_grant"


def test_network_asks_when_untrusted_at_net_tier(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.REPO_WRITE_NET, cwd=tmp_path)
    d = evaluate(_req(is_read_only=True, network_host="api.example.com"), pol)
    assert d.outcome is Outcome.ASK
    assert d.rule == "tier_trust"


def test_default_ask_for_undetermined_effectful_action(tmp_path: Path) -> None:
    pol = Policy(tier=SandboxTier.REPO_WRITE, cwd=tmp_path)
    d = evaluate(_req(is_read_only=False), pol)
    assert d.outcome is Outcome.ASK
    assert d.rule == "default"


# --- invariants ---


def test_guard_supremacy_invariant(tmp_path: Path) -> None:
    cred = Path.home() / ".aws" / "credentials"
    pol = Policy(tier=SandboxTier.UNRESTRICTED, cwd=tmp_path, tool_allow=frozenset({"t"}))
    d = evaluate(_req(is_read_only=True, target_paths=(cred,)), pol)
    assert d.outcome is Outcome.DENY


def test_evaluate_is_deterministic(tmp_path: Path) -> None:
    pol = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"t": SandboxTier.REPO_WRITE},
    )
    req = _req(target_paths=(tmp_path / "f.txt",))
    assert evaluate(req, pol) == evaluate(req, pol)
