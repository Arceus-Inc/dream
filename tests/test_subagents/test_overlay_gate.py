"""Typed permission overlay: write/network/execute/tool denies; never widens."""

from __future__ import annotations

from pathlib import Path

from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.subagents._overlay import PermissionOverlay
from dream.subagents._overlay_gate import wrap_permission_gate


def _allow(_request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision(outcome=Outcome.ALLOW, reason="parent allow", rule="test")


def _deny(_request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision(outcome=Outcome.DENY, reason="parent deny", rule="test")


def test_write_token_denies_mutating_request() -> None:
    gate = wrap_permission_gate(_allow, PermissionOverlay.parse(("write",)))
    decision = gate(
        PermissionRequest(tool_name="write_file", is_read_only=False, target_paths=(Path("a"),))
    )
    assert decision.outcome is Outcome.DENY
    assert "write" in decision.reason


def test_network_token_denies_network_host() -> None:
    gate = wrap_permission_gate(_allow, PermissionOverlay.parse(("network",)))
    decision = gate(
        PermissionRequest(tool_name="web_fetch", is_read_only=True, network_host="example.com")
    )
    assert decision.outcome is Outcome.DENY
    assert "network" in decision.reason
    allowed = gate(PermissionRequest(tool_name="read_file", is_read_only=True))
    assert allowed.outcome is Outcome.ALLOW


def test_execute_token_denies_bash_and_command() -> None:
    gate = wrap_permission_gate(_allow, PermissionOverlay.parse(("execute",)))
    bash = gate(PermissionRequest(tool_name="bash", is_read_only=False, command="ls"))
    assert bash.outcome is Outcome.DENY
    assert "execute" in bash.reason
    named = gate(PermissionRequest(tool_name="execute_code", is_read_only=False))
    assert named.outcome is Outcome.DENY


def test_unknown_token_is_a_tool_deny_not_a_grant() -> None:
    overlay = PermissionOverlay.parse(("web_search",))
    assert overlay.tools == frozenset({"web_search"})
    assert not overlay.write
    gate = wrap_permission_gate(_allow, overlay)
    denied = gate(PermissionRequest(tool_name="web_search", is_read_only=True))
    assert denied.outcome is Outcome.DENY
    other = gate(PermissionRequest(tool_name="read_file", is_read_only=True))
    assert other.outcome is Outcome.ALLOW


def test_overlay_cannot_widen_parent_deny() -> None:
    gate = wrap_permission_gate(_deny, PermissionOverlay())
    decision = gate(PermissionRequest(tool_name="write_file", is_read_only=False))
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "parent deny"


def test_repo_write_alias_is_write_capability() -> None:
    overlay = PermissionOverlay.parse(("repo-write+net-allowlist",))
    assert overlay.write
    assert "write" in overlay
    assert not overlay.network


def test_write_overlay_allows_read_only_file_tools() -> None:
    gate = wrap_permission_gate(_allow, PermissionOverlay.parse(("write",)))
    decision = gate(PermissionRequest(tool_name="read_file", is_read_only=True))
    assert decision.outcome is Outcome.ALLOW


def test_write_overlay_denies_read_only_classified_shell() -> None:
    """``echo`` / ``cat`` can still redirect; write overlay is fail-closed."""
    gate = wrap_permission_gate(_allow, PermissionOverlay.parse(("write",)))
    decision = gate(
        PermissionRequest(
            tool_name="bash",
            is_read_only=True,
            command="echo leaked > /tmp/escape.txt",
        )
    )
    assert decision.outcome is Outcome.DENY
    assert "write" in decision.reason


def test_network_overlay_denies_command_based_network() -> None:
    gate = wrap_permission_gate(_allow, PermissionOverlay.parse(("network",)))
    curl = gate(
        PermissionRequest(tool_name="bash", is_read_only=False, command="curl https://example.com")
    )
    assert curl.outcome is Outcome.DENY
    assert "network" in curl.reason
    code = gate(PermissionRequest(tool_name="execute_code", is_read_only=False))
    assert code.outcome is Outcome.DENY
