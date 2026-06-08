"""Spec 13A — permission value objects + enums (pure data, no IO)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from dream.permissions._types import (
    Effect,
    Outcome,
    PathRule,
    PermissionDecision,
    PermissionRequest,
    Policy,
    SandboxTier,
)


def test_sandbox_tier_is_ordered_by_capability() -> None:
    assert SandboxTier.READ_ONLY < SandboxTier.REPO_WRITE
    assert SandboxTier.REPO_WRITE < SandboxTier.REPO_WRITE_NET
    assert SandboxTier.REPO_WRITE_NET < SandboxTier.UNRESTRICTED


def test_outcome_has_three_variants() -> None:
    assert {o.name for o in Outcome} == {"ALLOW", "DENY", "ASK"}


def test_effect_has_write_and_network() -> None:
    assert {e.name for e in Effect} == {"WRITE", "NETWORK"}


def test_effect_carries_its_required_tier() -> None:
    assert Effect.WRITE.required_tier is SandboxTier.REPO_WRITE
    assert Effect.NETWORK.required_tier is SandboxTier.REPO_WRITE_NET


def test_effect_carries_a_human_label() -> None:
    assert Effect.WRITE.label == "write"
    assert Effect.NETWORK.label == "network"


def test_decision_allowed_only_for_allow() -> None:
    allow = PermissionDecision(Outcome.ALLOW, "ok", "tier")
    deny = PermissionDecision(Outcome.DENY, "no", "guard")
    ask = PermissionDecision(Outcome.ASK, "maybe", "default")
    assert allow.allowed is True
    assert deny.allowed is False
    assert ask.allowed is False


def test_request_defaults_are_empty() -> None:
    req = PermissionRequest(tool_name="read_file", is_read_only=True)
    assert req.target_paths == ()
    assert req.command is None
    assert req.network_host is None


def test_value_objects_are_frozen() -> None:
    req = PermissionRequest(tool_name="x", is_read_only=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.tool_name = "y"  # type: ignore[misc]


def test_policy_defaults() -> None:
    pol = Policy(tier=SandboxTier.REPO_WRITE, cwd=Path("/repo"))
    assert pol.required_tier == {}
    assert pol.tool_deny == frozenset()
    assert pol.tool_allow == frozenset()
    assert pol.path_deny == ()
    assert pol.command_deny == ()
    assert pol.extra_allowed == ()
    assert pol.credential_extra == ()


def test_path_rule_fields() -> None:
    rule = PathRule(pattern="secrets/**", allow=False)
    assert rule.pattern == "secrets/**"
    assert rule.allow is False
